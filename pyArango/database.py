import json
import types

from . import collection as COL
from . import consts as CONST
from . import graph as GR

from .document import Document
from .graph import Graph
from .query import AQLQuery
from .theExceptions import CreationError, UpdateError, AQLQueryError, TransactionError

__all__ = ["Database", "DBHandle"]

class Database(object) :
    """Databases are meant to be instanciated by connections"""

    def __init__(self, connection, name) :

        self.name = name
        self.connection = connection
        self.collections = {}


        self.collections = {}
        self.graphs = {}

        self.reload()

    def getURL(self) :
        return '%s/_db/%s/_api' % (self.connection.getEnpointURL(), self.name)

    def getCollectionsURL(self) :
        return '%s/collection' % (self.getURL())
    
    def getCursorsURL()(self) :
        return '%s/cursor' % (self.getURL())
        
    def getExplainURL(self) :
        return '%s/explain' % (self.getURL())
        
    def getGraphsURL(self) :
        return "%s/gharial" % self.getURL()
    
    def getTransactionURL(self) :
        return  "%s/transaction" % self.getURL()
    
    def reloadCollections(self) :
        "reloads the collection list."
        r = self.connection.session.get(self.getCollectionsURL())
        data = r.json()
        if r.status_code == 200 :
            self.collections = {}

            for colData in data["result"] :
                colName = colData['name']
                if colData['isSystem'] :
                    colObj = COL.SystemCollection(self, colData)
                else :
                    try :
                        colClass = COL.getCollectionClass(colName)
                        colObj = colClass(self, colData)
                    except KeyError :
                        if colData["type"] == CONST.COLLECTION_EDGE_TYPE :
                            colObj = COL.Edges(self, colData)
                        elif colData["type"] == CONST.COLLECTION_DOCUMENT_TYPE :
                            colObj = COL.Collection(self, colData)
                        else :
                            print(("Warning!! Collection of unknown type: %d, trying to load it as Collection nonetheless." % colData["type"]))
                            colObj = COL.Collection(self, colData)

                self.collections[colName] = colObj
        else :
            raise UpdateError(data["errorMessage"], data)

    def reloadGraphs(self) :
        "reloads the graph list"
        r = self.connection.session.get(self.getGraphsURL())
        data = r.json()
        if r.status_code == 200 :
            self.graphs = {}
            for graphData in data["graphs"] :
                try :
                    self.graphs[graphData["_key"]] = GR.getGraphClass(graphData["_key"])(self, graphData)
                except KeyError :
                    self.graphs[graphData["_key"]] = Graph(self, graphData)
        else :
            raise UpdateError(data["errorMessage"], data)

    def reload(self) :
        "reloads collections and graphs"
        self.reloadCollections()
        self.reloadGraphs()

    def createCollection(self, className = 'Collection', **colProperties) :
        """Creates a collection and returns it.
        ClassName the name of a class inheriting from Collection or Egdes, it can also be set to 'Collection' or 'Edges' in order to create untyped collections of documents or edges.
        Use colProperties to put things such as 'waitForSync = True' (see ArangoDB's doc
        for a full list of possible arugments). If a '_properties' dictionary is defined in the collection schema, arguments to this function overide it"""

        colClass = COL.getCollectionClass(className)

        if len(colProperties) > 0 :
            colProperties = dict(colProperties)
        else :
            try :
                colProperties = dict(colClass._properties)
            except AttributeError :
                colProperties = {}

        if className != 'Collection' and className != 'Edges' :
            colProperties['name'] = className
        else :
            if 'name' not in colProperties :
                raise ValueError("a 'name' argument mush be supplied if you want to create a generic collection")

        if colProperties['name'] in self.collections :
            raise CreationError("Database %s already has a collection named %s" % (self.name, colProperties['name']) )

        if issubclass(colClass, COL.Edges) or colClass.__class__ is COL.Edges:
            colProperties["type"] = CONST.COLLECTION_EDGE_TYPE
        else :
            colProperties["type"] = CONST.COLLECTION_DOCUMENT_TYPE

        payload = json.dumps(colProperties, default=str)
        r = self.connection.session.post(self.getCollectionsURL(), data = payload)
        data = r.json()

        if r.status_code == 200 and not data["error"] :
            col = colClass(self, data)
            self.collections[col.name] = col
            return self.collections[col.name]
        else :
            raise CreationError(data["errorMessage"], data)

    def fetchDocument(self, _id) :
        "fetchs a document using it's _id"
        sid = _id.split("/")
        return self[sid[0]][sid[1]]

    def createGraph(self, name, createCollections = True, isSmart = False, numberOfShards = None, smartGraphAttribute = None) :
        """Creates a graph and returns it. 'name' must be the name of a class inheriting from Graph.
        Checks will be performed to make sure that every collection mentionned in the edges definition exist. Raises a ValueError in case of
        a non-existing collection."""

        def _checkCollectionList(lst) :
            for colName in lst :
                if not COL.isCollection(colName) :
                    raise ValueError("'%s' is not a defined Collection" % colName)

        graphClass = GR.getGraphClass(name)

        ed = []
        for e in graphClass._edgeDefinitions :
            if not COL.isEdgeCollection(e.edgesCollection) :
                raise ValueError("'%s' is not a defined Edge Collection" % e.edgesCollection)
            _checkCollectionList(e.fromCollections)
            _checkCollectionList(e.toCollections)

            ed.append(e.toJson())

        _checkCollectionList(graphClass._orphanedCollections)

        options = {}
        if numberOfShards:
            options['numberOfShards'] = numberOfShards
        if smartGraphAttribute:
            options['smartGraphAttribute'] = smartGraphAttribute

        payload = {
                "name": name,
                "edgeDefinitions": ed,
                "orphanCollections": graphClass._orphanedCollections
            }

        if isSmart :
                payload['isSmart'] = isSmart

        if options:
            payload['options'] = options

        payload = json.dumps(payload)

        r = self.connection.session.post(self.getGraphsURL(), data = payload)
        data = r.json()

        if r.status_code == 201 or r.status_code == 202 :
            self.graphs[name] = graphClass(self, data["graph"])
        else :
            raise CreationError(data["errorMessage"], data)
        return self.graphs[name]

    def hasCollection(self, name) :
        """returns true if the databse has a collection by the name of 'name'"""
        return name in self.collections

    def hasGraph(self, name):
        """returns true if the databse has a graph by the name of 'name'"""
        return name in self.graphs

    def dropAllCollections(self):
        """drops all public collections (graphs included) from the database"""
        for graph_name in self.graphs:
            self.graphs[graph_name].delete()
        for collection_name in self.collections:
            # Collections whose name starts with '_' are system collections
            if not collection_name.startswith('_'):
                self[collection_name].delete()
        return

    def AQLQuery(self, query, batchSize = 100, rawResults = False, bindVars = {}, options = {}, count = False, fullCount = False,
                 json_encoder = None, **moreArgs) :
        """Set rawResults = True if you want the query to return dictionnaries instead of Document objects.
        You can use **moreArgs to pass more arguments supported by the api, such as ttl=60 (time to live)"""
        return AQLQuery(self, query, rawResults = rawResults, batchSize = batchSize, bindVars  = bindVars, options = options, count = count, fullCount = fullCount,
                        json_encoder = json_encoder, **moreArgs)

    def explainAQLQuery(self, query, bindVars={}, allPlans = False) :
        """Returns an explanation of the query. Setting allPlans to True will result in ArangoDB returning all possible plans. False returns only the optimal plan"""
        payload = {'query' : query, 'bindVars' : bindVars, 'allPlans' : allPlans}
        request = self.connection.session.post(self.getExplainURL(), data = json.dumps(payload, default=str))
        return request.json()

    def validateAQLQuery(self, query, bindVars = None, options = None) :
        "returns the server answer is the query is valid. Raises an AQLQueryError if not"
        if bindVars is None :
            bindVars = {}
        if options is None :
            options = {}
        payload = {'query' : query, 'bindVars' : bindVars, 'options' : options}
        r = self.connection.session.post(self.getCursorsURL(), data = json.dumps(payload, default=str))
        data = r.json()
        if r.status_code == 201 and not data["error"] :
            return data
        else :
            raise AQLQueryError(data["errorMessage"], query, data)

    def transaction(self, collections, action, waitForSync = False, lockTimeout = None, params = None) :
        """Execute a server-side transaction"""
        payload = {
                "collections": collections,
                "action": action,
                "waitForSync": waitForSync}
        if lockTimeout is not None:
                payload["lockTimeout"] = lockTimeout
        if params is not None:
            payload["params"] = params

        self.connection.reportStart(action)

        r = self.connection.session.post(self.geTransactionURL(), data = json.dumps(payload, default=str))

        self.connection.reportItem()

        data = r.json()

        if (r.status_code == 200 or r.status_code == 201 or r.status_code == 202) and not data.get("error") :
            return data
        else :
            raise TransactionError(data["errorMessage"], action, data)

    def __repr__(self) :
        return "ArangoDB database: %s" % self.name

    def __getitem__(self, collectionName) :
        """use database[collectionName] to get a collection from the database"""
        try :
            return self.collections[collectionName]
        except KeyError :
            self.reload()
            try :
                return self.collections[collectionName]
            except KeyError :
                raise KeyError("Can't find any collection named : %s" % collectionName)

class DBHandle(Database) :
    "As the loading of a Database also triggers the loading of collections and graphs within. Only handles are loaded first. The full database are loaded on demand in a fully transparent manner."
    def __init__(self, connection, name) :
        self.connection = connection
        self.name = name

    def __getattr__(self, k) :
        name = Database.__getattribute__(self, 'name')
        connection = Database.__getattribute__(self, 'connection')
        Database.__init__(self, connection, name)
        return Database.__getattribute__(self, k)
