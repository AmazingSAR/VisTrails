###############################################################################
##
## Copyright (C) 2014-2015, New York University.
## Copyright (C) 2011-2014, NYU-Poly.
## Copyright (C) 2006-2011, University of Utah.
## All rights reserved.
## Contact: contact@vistrails.org
##
## This file is part of VisTrails.
##
## "Redistribution and use in source and binary forms, with or without
## modification, are permitted provided that the following conditions are met:
##
##  - Redistributions of source code must retain the above copyright notice,
##    this list of conditions and the following disclaimer.
##  - Redistributions in binary form must reproduce the above copyright
##    notice, this list of conditions and the following disclaimer in the
##    documentation and/or other materials provided with the distribution.
##  - Neither the name of the New York University nor the names of its
##    contributors may be used to endorse or promote products derived from
##    this software without specific prior written permission.
##
## THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
## AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO,
## THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
## PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR
## CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
## EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
## PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS;
## OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
## WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR
## OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF
## ADVISED OF THE POSSIBILITY OF SUCH DAMAGE."
##
###############################################################################
from __future__ import division, with_statement

import copy
from datetime import datetime
import inspect
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

from vistrails.core import debug
from vistrails.core.system import get_elementtree_library, temporary_directory,\
     execute_cmdline, systemType, get_executable_path, strftime
from vistrails.core.bundles import py_import
from vistrails.core.utils import Chdir
from vistrails.core.modules.sub_module import get_cur_abs_namespace,\
    parse_abstraction_name, read_vistrail_from_db
import vistrails.core.requirements
import vistrails.core.system

from vistrails.db import VistrailsDBException
from vistrails.db.persistence import sql
alchemy = sql.alchemy
from vistrails.db.domain import DBVistrail, DBWorkflow, DBLog, DBAbstraction, DBGroup, \
    DBRegistry, DBWorkflowExec, DBOpmGraph, DBProvDocument, DBAnnotation, \
    DBMashuptrail, DBStartup
import vistrails.db.services.abstraction
from vistrails.db.services.bundle import FileManifest
from vistrails.db.services.bundle_legacy import LegacyZIPSerializer, \
    LegacyDirSerializer
# from vistrails.db.services.bundle import DefaultVistrailsZIPSerializer , WorkflowXMLSerializer, \
#     BundleObj, VistrailBundle, WorkflowBundle, LogBundle, RegistryBundle
import vistrails.db.services.log
import vistrails.db.services.opm
import vistrails.db.services.prov
import vistrails.db.services.registry
import vistrails.db.services.workflow
import vistrails.db.services.vistrail
from vistrails.db.versions import getVersionDAO, currentVersion, \
    get_sql_utils, get_version_name, translate_vistrail, translate_workflow, \
    translate_log, translate_registry, translate_startup

ElementTree = get_elementtree_library()

CONNECT_TIMEOUT = 15

class SaveBundle(object):
    """Transient bundle of objects to be saved or loaded.
       The bundle type MUST be specified in the constructor; it should be
       the the vtType of the primary object in the bundle. This parameter
       identifies which object is the primary object when mutiple objects
       are stored in the bundle.

       Args is the (unordered) list of objects to be included in the bundle
       (vistrail, workflow, log, registry, opm_graph).  Any args without a
       'vtType' attribute are explicitly ignored (including any args=None).

       As kwargs, you can specify 'abstractions=[]' or 'thumbnails=[]',
       both of which should be a list of filenames as strings.  You can also
       specify the other bundle objects as kwargs, but abstractions and
       thumbnails cannot be args, since they are both lists, and there is no
       vtType to differentiate between them.

       As a final option, you can directly set the objects in the bundle,
       self.vistrail = vistrail_object, self.thumbnails = thumbs_list, etc.,
       before passing the SaveBundle to a locator.  Both abstractions and
       thumbnails are intialized for convenience so that you can directly
       append to them when using this step-by-step bundle creation method.

    """

    def __init__(self, bundle_type, *args, **kwargs):
        self.bundle_type = bundle_type
        self.vistrail = None
        self.workflow = None
        self.log = None
        self.registry = None
        self.opm_graph = None
        self.abstractions = []
        self.thumbnails = []
        self.mashups = []
        # Make all args into attrs using vtType as attr name
        # This requires that attr names in this class match the vtTypes
        # i.e. if arg's vtType is 'vistrail', self.vistrail = arg, etc...
        for arg in args:
            if hasattr(arg, 'vtType'):
                setattr(self, arg.vtType, arg)
        # Make all keyword args directly into attrs
        for (k,v) in kwargs.iteritems():
            setattr(self, k, v)

    def get_db_objs(self):
        """Gets a list containing only the DB* objects in the bundle"""
        return [obj for obj in self.__dict__.itervalues() if obj is not None and not isinstance(obj, (list, basestring))]

    def get_primary_obj(self):
        """get_primary_obj() -> DB*
           Gets the bundle's primary DB* object based on the bundle type.
        """
        return getattr(self, self.bundle_type)

    def __copy__(self):
        return SaveBundle.do_copy(self)
    
    def do_copy(self):
        cp = SaveBundle(self.bundle_type)
        cp.vistrail = copy.copy(self.vistrail)
        cp.workflow = copy.copy(self.workflow)
        cp.log = copy.copy(self.log)
        cp.registry = copy.copy(self.registry)
        cp.opm_graph = copy.copy(self.opm_graph)
        for a in self.abstractions:
            cp.abstractions.append(a)
        
        for t in self.thumbnails:
            cp.thumbnails.append(t)

        for m in self.mashups:
            cp.mashups.append(m)
        
        return cp

##############################################################################
# Versioned I/O

def get_sqlalchemy():
    return py_import('sqlalchemy',
                     {'pip': 'SQLAlchemy',
                        'linux-debian': 'python-sqlalchemy',
                        'linux-ubuntu': 'python-sqlalchemy',
                        'linux-fedora': 'python-sqlalchemy'})

def default_open_db_connection(config):
    sqlalchemy = get_sqlalchemy()

    if config is None:
        msg = "Need to provide a valid configuration dictionary or string"
        raise VistrailsDBException(msg)
    if type(config) == dict:
        if config.get("dialect") is not None:
            driver = config["dialect"]
        else:
            driver = "mysql"
        if config.get("driver") is not None:
            driver += "+%s" % config["driver"]
        url = sqlalchemy.engine.url.URL(driver, 
                                        config.get("user"), 
                                        config.get("passwd"), 
                                        config.get("host"), 
                                        config.get("port"), 
                                        config.get("db"))
        engine = sqlalchemy.create_engine(url)
    else:
        engine = sqlalchemy.create_engine(config)
    try:
        connection = engine.connect()
    except Exception, e:
        raise VistrailsDBException("Unable to open db connection: %s" % str(e))
    return connection

def get_db_version_from_db(db_connection, fail=False):
    sqlalchemy = get_sqlalchemy()

    if isinstance(db_connection, sqlalchemy.engine.interfaces.Connectable):
        db_connection = db_connection.connection
    c = db_connection.cursor()
    try:
        c.execute("select version from vistrails_version;")
        version = c.fetchone()[0]
        return version
    except Exception, e:
        if fail:
            raise
        debug.warning('Cannot obtain current db version, using current (%s). '
                      'Exception: "%s"' % (currentVersion, str(e)))
    finally:
        c.close()
    return currentVersion

def get_db_version(db_connection):
    try:
        return db_connection.__vt_db_version__
    except AttributeError:
        version = get_db_version_from_db(db_connection)
        db_connection.__vt_db_version__ = version
        return version
    return None

# run_versioned decorator
def run_versioned(f):
    """The run_versioned decorator is special in that it does not actually
    run the method but rather the version's version of that method.

    """

    def wrapper(db_connection, *args, **kwargs):
        if db_connection is None:
            return f(db_connection, *args, **kwargs)
        version = get_db_version(db_connection)
        utils = get_sql_utils(version)
        return getattr(utils, f.__name__)(db_connection, *args, **kwargs)
    return wrapper

def open_db_connection(config):
    # FIXME need to make this more general (just connect_str) here so
    # that it is more straightforward to support other types of
    # databases
    if config is None:
        msg = "You need to provide valid config dictionary"
        raise VistrailsDBException(msg)
    if 'connect_timeout' not in config:
        config['connect_timeout'] = CONNECT_TIMEOUT

    if "version" in config:
        version = config["version"]
        del config["version"]
    else:
        default_conn = default_open_db_connection(config)
        version = get_db_version(default_conn)
    utils = get_sql_utils(version)
    conn = utils.open_db_connection(config)
    conn.__vt_db_version__ = version
    return conn

@run_versioned
def close_db_connection(db_connection):
    pass

def test_db_connection(config):
    """testDBConnection(config: dict) -> None
    Tests a connection raising an exception in case of error.
    
    """
    #print "Testing config", config
    if 'connect_timeout' not in config:
        config['connect_timeout'] = CONNECT_TIMEOUT
    try:
        db_connection = open_db_connection(config)
        close_db_connection(db_connection)
    except Exception, e:
        msg = "connection test failed (%s)" %str(e)
        raise VistrailsDBException(msg)

@run_versioned
def ping_db_connection(db_connection):
    """ping_db_connection(db_connection) -> boolean 
    It will ping the database to check if the connection is alive.
    It returns True if it is, False otherwise. 
    This can be used for preventing the "MySQL Server has gone away" error. 
    """
    pass

@run_versioned
def get_current_time(db_connection=None):
    return datetime.now()

@run_versioned
def get_db_object_list(db_connection, obj_type):
    pass

@run_versioned
def get_db_object_modification_time(db_connection, obj_id, obj_type):
    pass

@run_versioned
def get_db_object_version(db_connection, obj_id, obj_type):
    pass

@run_versioned
def get_db_id_from_name(db_connection, obj_type, name):
    pass

@run_versioned
def get_db_abstraction_modification_time(db_connection, abstraction):
    pass
    
@run_versioned
def get_db_ids_from_vistrail(db_connection, vt_id, id_key):
    """ get_db_ids_from_vistrail(db_connection: DBConnection,
                                 vt_id: int, id_key: str): List
        Returns object ids associated with a vistrail by an annotation
    """
    pass

def get_db_abstraction_ids_from_vistrail(db_connection, vt_id):
    """ get_db_abstractions_from_vistrail(db_connection: DBConnection,
                                          vt_id: int): List
        Returns abstractions associated with a vistrail
    """
    
    id_key = '__abstraction_vistrail_id__'
    return get_db_ids_from_vistrail(db_connection, vt_id, id_key)

def get_db_mashuptrail_ids_from_vistrail(db_connection, vt_id):
    """ get_db_mashuptrail_ids_from_vistrail(db_connection: DBConnection,
                                          vt_id: int): List
        Returns mashuptrails associated with a vistrail
    """
    id_key = '__mashuptrail_vistrail_id__'
    return get_db_ids_from_vistrail(db_connection, vt_id, id_key)

@run_versioned
def get_db_ids_from_log(db_connection, vt_id):
    pass

@run_versioned
def get_matching_abstraction_id(db_connection, abstraction):
    pass

@run_versioned
def create_db_tables(db_connection):
    pass

@run_versioned
def drop_db_tables(db_connection):
    pass

@run_versioned
def get_saved_workflows(db_connection, vistrail_id):
    """ Returns list of action ids representing populated workflows """
    pass

@run_versioned
def get_thumbnail_fnames_from_db(db_connection, obj_id, obj_type):
    pass

@run_versioned
def get_thumbnail_data_from_db(db_connection, fname):
    pass

@run_versioned
def get_existing_thumbnails_in_db(db_connection, fnames):
    pass

@run_versioned
def insert_thumbnails_into_db(db_connection, abs_fnames):
    pass

##############################################################################
# General I/O

custom_file_serializers = {}
def register_custom_file_serializer(obj_type, s):
    if obj_type in custom_file_serializers:
        raise VistrailsDBException('Serializer for type "%s" already exists' %
                                   obj_type)
    custom_file_serializers[obj_type] = s

def add_custom_file_serializers(s):
    for k, v in custom_file_serializers.iteritems():
        s.add_serializer(k,v)

def get_dir_bundle_serializer(dir_path=None, bundle=None, version=None):
    #FIXME hardcoding manifest filename
    s = None
    if dir_path is None or not os.path.exists(dir_path):
        if version is None:
            version = currentVersion
        s = vistrails.db.versions.get_dir_bundle_serializer(version, dir_path,
                                                            bundle)
    else:
        manifest_fname = os.path.join(dir_path, "MANIFEST")
        if os.path.exists(manifest_fname):
            manifest = FileManifest(dir_path)
            manifest.load()
            if manifest.version:
                s = vistrails.db.versions.get_dir_bundle_serializer(
                    manifest.version, dir_path, bundle)
    if not s:
        # use legacy serializer
        s = LegacyDirSerializer(dir_path, bundle=bundle)
    add_custom_file_serializers(s)
    return s

def get_zip_bundle_serializer(fname=None, bundle=None, version=None):
    s = None
    manifest_fname = None
    not_zip = True
    if fname is not None and os.path.exists(fname):
        tempd = tempfile.mkdtemp(prefix="vt_manifest_test")
        try:
            zf = zipfile.ZipFile(fname)
            not_zip = False
            manifest_fname = zf.extract("MANIFEST", tempd)
            if manifest_fname:
                manifest = FileManifest(dir_path=tempd)
                manifest.load()
                if manifest.version:
                    s = vistrails.db.versions.get_zip_bundle_serializer(
                        manifest.version, fname, bundle)
        except KeyError:
            pass
        finally:
            shutil.rmtree(tempd)
    # catch cases where the file exists but is not a zip file,
    # e.g. for mkstemp files
    if not_zip:
        if version is None:
            version = currentVersion
        print "GETTING VERSION", version
        s = vistrails.db.versions.get_zip_bundle_serializer(version, fname,
                                                            bundle)
        print "S:", s
    if not s:
        # use legacy serializer
        s = LegacyZIPSerializer(fname, bundle=bundle)
    add_custom_file_serializers(s)
    return s

def get_db_bundle_serializer(conn, bundle=None, version=None):
    pass

def open_from_xml(filename, type):
    if type == DBVistrail.vtType:
        return open_vistrail_from_xml(filename)
    elif type == DBWorkflow.vtType:
        return open_workflow_from_xml(filename)
    elif type == DBLog.vtType:
        return open_log_from_xml(filename)
    elif type == DBRegistry.vtType:
        return open_registry_from_xml(filename)
    else:
        raise VistrailsDBException("cannot open object of type "
                                   "'%s' from xml" % type)

def save_to_xml(obj, filename, version=None):
    if obj.vtType == DBVistrail.vtType:
        return save_vistrail_to_xml(obj, filename, version)
    elif obj.vtType == DBWorkflow.vtType:
        return save_workflow_to_xml(obj, filename, version)
    elif obj.vtType == DBLog.vtType:
        return save_log_to_xml(obj, filename, version)
    elif obj.vtType == DBRegistry.vtType:
        return save_registry_to_xml(obj, filename, version)
    elif obj.vtType == DBOpmGraph.vtType:
        return save_opm_to_xml(obj, filename, version)
    elif obj.vtType == DBProvDocument.vtType:
        return save_prov_to_xml(obj, filename, version)
    else:
        raise VistrailsDBException("cannot save object of type "
                                   "'%s' to xml" % type)

def open_bundle_from_zip_xml(bundle_type, filename):
    if bundle_type == DBVistrail.vtType:
        return open_vistrail_bundle_from_zip_xml(filename)
    else:
        raise VistrailsDBException("cannot open bundle of type '%s' from zip" %\
                                       bundle_type)

def save_bundle_to_zip_xml(bundle, filename, tmp_dir=None, version=None):
    if isinstance(bundle, VistrailBundle):
        return save_vistrail_bundle_to_zip_xml(bundle, filename, tmp_dir, version)
    elif isinstance(bundle, LogBundle):
        return save_log_bundle_to_xml(bundle, filename, version)
    elif isinstance(bundle, WorkflowBundle):
        return save_workflow_bundle_to_xml(bundle, filename, version)
    elif isinstance(bundle, RegistryBundle):
        return save_registry_bundle_to_xml(bundle, filename, version)
    else:
        raise VistrailsDBException("cannot save bundle of type '%s' to zip" % \
                                       type(bundle).__name__)

def open_bundle_from_db(bundle_type, connection, primary_obj_id, tmp_dir=None):
    if bundle_type == DBVistrail.vtType:
        return open_vistrail_bundle_from_db(connection, primary_obj_id, tmp_dir)
    else:
        raise VistrailsDBException("cannot open bundle of type '%s' from db" %\
                                       bundle_type)

def save_bundle_to_db(save_bundle, connection, do_copy=False, version=None):
    bundle_type = save_bundle.bundle_type
    if bundle_type == DBVistrail.vtType:
        return save_vistrail_bundle_to_db(save_bundle, connection, do_copy, version)
    elif bundle_type == DBLog.vtType:
        return save_log_bundle_to_db(save_bundle, connection, do_copy, version)
    elif bundle_type == DBWorkflow.vtType:
        return save_workflow_bundle_to_db(save_bundle, connection, do_copy, version)
    elif bundle_type == DBRegistry.vtType:
        return save_registry_bundle_to_db(save_bundle, connection, do_copy, version)
    else:
        raise VistrailsDBException("cannot save bundle of type '%s' to db" % \
                                       bundle_type)

def open_from_db(db_connection, type, obj_id):
    if type == DBVistrail.vtType:
        return open_vistrail_from_db(db_connection, obj_id)
    elif type == DBWorkflow.vtType:
        return open_workflow_from_db(db_connection, obj_id)
    elif type == DBLog.vtType:
        return open_log_from_db(db_connection, obj_id)
    elif type == DBRegistry.vtType:
        return open_registry_from_db(db_connection, obj_id)
    else:
        raise VistrailsDBException("cannot open object of type '%s' from db" % \
                                       type)

def save_to_db(obj, db_connection, do_copy=False):
    if obj.vtType == DBVistrail.vtType:
        return save_vistrail_to_db(obj, db_connection, do_copy)
    elif obj.vtType == DBWorkflow.vtType:
        return save_workflow_to_db(obj, db_connection, do_copy)
    elif obj.vtType == DBLog.vtType:
        return save_log_to_db(obj, db_connection, do_copy)
    elif obj.vtType == DBRegistry.vtType:
        return save_registry_to_db(obj, db_connection, do_copy)
    else:
        raise VistrailsDBException("cannot save object of type '%s' to db" % \
                                       type)

def delete_from_db(db_connection, type, obj_id):
    if type in [DBVistrail.vtType, DBWorkflow.vtType, DBLog.vtType,
                DBRegistry.vtType]:
        return delete_entity_from_db(db_connection, type, obj_id)

def close_zip_xml(temp_dir):
    """close_zip_xml(temp_dir: string) -> None
    Removes any temporary files for a vistrails file

    temp_dir: directory storing any persistent files
    """
    if temp_dir is None:
        return
    if not os.path.isdir(temp_dir):
        if os.path.isfile(temp_dir):
            os.remove(temp_dir)

        # cleanup has already happened
        return
    try:
        for root, dirs, files in os.walk(temp_dir, topdown=False):
            for name in files:
                os.remove(os.path.join(root, name))
            for name in dirs:
                os.rmdir(os.path.join(root, name))
        os.rmdir(temp_dir)
    except OSError, e:
        raise VistrailsDBException("Can't remove %s: %s" % (temp_dir, str(e)))

def serialize(object):
    daoList = getVersionDAO(currentVersion)
    return daoList.serialize(object)

def unserialize(str, obj_type):
    daoList = getVersionDAO(currentVersion)
    return daoList.unserialize(str, obj_type)
 
##############################################################################
# Vistrail I/O

def open_vistrail_from_xml(filename):
    """open_vistrail_from_xml(filename) -> Vistrail"""
    tree = ElementTree.parse(filename)
    version = get_version_for_xml(tree.getroot())
    try:
        daoList = getVersionDAO(version)
        vistrail = daoList.open_from_xml(filename, DBVistrail.vtType, tree)
        if vistrail is None:
            raise VistrailsDBException("Couldn't read vistrail from XML")
        vistrail = translate_vistrail(vistrail, version)
        vistrails.db.services.vistrail.update_id_scope(vistrail)
    except VistrailsDBException, e:
        if str(e).startswith('VistrailsDBException: Cannot find DAO for'):
            raise VistrailsDBException(
                "This vistrail was created by a newer version of VisTrails "
                "and cannot be opened.")
        raise e

    return vistrail

def open_vistrail_bundle_from_zip_xml(filename):
    """open_vistrail_bundle_from_zip_xml(filename) -> (Bundle, vt_save_dir)
    Open a vistrail from a zip compressed format.
    It expects that the vistrail file inside archive has name 'vistrail',
    the log inside archive has name 'log',
    abstractions inside archive have prefix 'abstraction_' in 'abstractions' dir,
    and thumbnails inside archive are '.png' files in 'thumbs' dir

    """

    # vt_save_dir = tempfile.mkdtemp(prefix='vt_save')
    # serializer = DefaultVistrailsZIPSerializer(dir_path=vt_save_dir)
    # bundle = serializer.load(filename)
    # return (bundle, vt_save_dir)
    s = get_zip_bundle_serializer(filename)
    return s.load()

def open_vistrail_bundle_from_db(db_connection, vistrail_id, tmp_dir=None):
    """open_vistrail_bundle_from_db(db_connection, id: long, tmp_dir: str) -> SaveBundle
       Open a vistrail bundle from the database.

    """
    vt_abs_dir = tempfile.mkdtemp(prefix='vt_abs')
    vistrail = open_vistrail_from_db(db_connection, vistrail_id)
    # FIXME open log from db
    log = None
    # open abstractions from db
    abstractions = []
    try:
        for abs_id in get_db_abstraction_ids_from_vistrail(db_connection, vistrail.db_id):
            abs = read_vistrail_from_db(db_connection, abs_id, vistrail.db_version)
            abs_fname = '%s%s(%s)%s' % ('abstraction_', abs.db_name, 
                                      get_cur_abs_namespace(abs), '.xml')
            fname = os.path.join(vt_abs_dir, abs_fname)
            save_vistrail_to_xml(abs, fname)
            abstractions.append(fname)
    except Exception, e:
        debug.critical('Could not load abstraction from database: %s' % str(e),
                       debug.format_exc())
    # open mashuptrails from db
    mashuptrails = []
    try:
        for mashup_id in get_db_mashuptrail_ids_from_vistrail(db_connection, vistrail.db_id):
            mashup = open_mashuptrail_from_db(db_connection, mashup_id)
            mashuptrails.append(mashup)
    except Exception, e:
        debug.critical('Could not load mashuptrail from database: %s' % str(e),
                       debug.format_exc())
    thumbnails = open_thumbnails_from_db(db_connection, DBVistrail.vtType,
                                         vistrail_id, tmp_dir)
    return SaveBundle(DBVistrail.vtType, vistrail, log,
                      abstractions=abstractions, thumbnails=thumbnails,
                      mashups=mashuptrails)

def open_vistrail_from_db(db_connection, id, lock=False, version=None):
    """open_vistrail_from_db(db_connection, id : long, lock: bool, 
                             version: str) 
         -> DBVistrail 

    """
    if db_connection is None:
        msg = "Need to call open_db_connection() before reading"
        raise VistrailsDBException(msg)
    if version is None:
        version = get_db_object_version(db_connection, id, DBVistrail.vtType)
    dao_list = getVersionDAO(version)
    vistrail = \
        dao_list.open_from_db(db_connection, DBVistrail.vtType, id, lock)
    vistrail = translate_vistrail(vistrail, version)
    for db_action in vistrail.db_get_actions():
        db_action.db_operations.sort(key=lambda x: x.db_id)
    vistrails.db.services.vistrail.update_id_scope(vistrail)
    return vistrail

def save_vistrail_to_xml(vistrail, filename, version=None):
    tags = {'xmlns:xsi': 'http://www.w3.org/2001/XMLSchema-instance',
            'xsi:schemaLocation': 'http://www.vistrails.org/vistrail.xsd'
            }
    if version is None:
        version = currentVersion
    if not vistrail.db_version:
        vistrail.db_version = currentVersion

    # current_action holds the current action id 
    # (used by the controller--write_vistrail)
    current_action = 0L
    if hasattr(vistrail, 'db_currentVersion'):
        current_action = vistrail.db_currentVersion

    vistrail = translate_vistrail(vistrail, vistrail.db_version, version)

    daoList = getVersionDAO(version)        
    daoList.save_to_xml(vistrail, filename, tags, version)
    vistrail = translate_vistrail(vistrail, version)
    vistrail.db_currentVersion = current_action
    return vistrail

def save_vistrail_bundle_to_zip_xml(bundle, filename, vt_save_dir=None, version=None):
    """save_vistrail_bundle_to_zip_xml(bundle: Bundle, filename: str,
                                vt_save_dir: str, version: str)
         -> (bundle: Bundle, vt_save_dir: str)

    bundle: a Bundle object containing vistrail data to save
    filename: filename to save to
    vt_save_dir: directory storing any previous files

    Generates a zip compressed version of vistrail.
    It raises an Exception if there was an error.

    """

    if bundle.vistrail is None:
        raise VistrailsDBException('save_vistrail_bundle_to_zip_xml failed, '
                                   'bundle does not contain a vistrail')
    if not vt_save_dir:
        vt_save_dir = tempfile.mkdtemp(prefix='vt_save')

    serializer = get_zip_bundle_serializer(filename, dir_path=vt_save_dir,
                                           bundle=bundle)
    serializer.save(filename)

    return (bundle, vt_save_dir)

def save_vistrail_bundle_to_db(save_bundle, db_connection, do_copy=False, version=None, save_wfs=True):
    if save_bundle.vistrail is None:
        raise VistrailsDBException('save_vistrail_bundle_to_db failed, '
                                   'bundle does not contain a vistrail')
    vistrail = save_vistrail_to_db(save_bundle.vistrail, db_connection, do_copy, version, save_wfs)
    log = None
    if save_bundle.vistrail.db_log_filename is not None:
        if save_bundle.log is not None:
            log = merge_logs(save_bundle.log,
                             save_bundle.vistrail.db_log_filename)
        else:
            log = open_log_from_xml(save_bundle.vistrail.db_log_filename, True)
    elif save_bundle.log is not None:
        log = save_bundle.log
    if log is not None:
        # Set foreign key 'vistrail_id' for the log to point at its vistrail
        log.db_vistrail_id = vistrail.db_id
        log = save_log_to_db(log, db_connection, do_copy, version)
    save_abstractions_to_db(save_bundle.abstractions, vistrail.db_id, db_connection, do_copy)
    save_mashuptrails_to_db(save_bundle.mashups, vistrail.db_id, db_connection, do_copy)
    save_thumbnails_to_db(save_bundle.thumbnails, db_connection)
    return SaveBundle(DBVistrail.vtType, vistrail, log,
                      abstractions=list(save_bundle.abstractions),
                      thumbnails=list(save_bundle.thumbnails),
                      mashups=list(save_bundle.mashups))

def save_vistrail_to_db(vistrail, db_connection, do_copy=False, version=None,
                        save_wfs=True):
    if db_connection is None:
        msg = "Need to call open_db_connection() before reading"
        raise VistrailsDBException(msg)
    if version is None:
        version = get_db_version(db_connection)
        if version is None:
            version = currentVersion
    if not vistrail.db_version:
        vistrail.db_version = currentVersion

    dao_list = getVersionDAO(version)
    utils = get_sql_utils(version)

    trans = utils.start_transaction(db_connection)
    
    # current_action holds the current action id 
    # (used by the controller--write_vistrail)
    current_action = 0L
    if hasattr(vistrail, 'db_currentVersion'):
        current_action = vistrail.db_currentVersion

    if not do_copy and vistrail.db_last_modified is not None:
        new_time = get_db_object_modification_time(db_connection, 
                                                   vistrail.db_id,
                                                   DBVistrail.vtType)
        if new_time > vistrail.db_last_modified:
            # need synchronization
            old_vistrail = open_vistrail_from_db(db_connection,
                                                 vistrail.db_id,
                                                 True, version)
            old_vistrail = translate_vistrail(old_vistrail, version)
            # the "old" one is modified and changes integrated
            current_action = \
                vistrails.db.services.vistrail.synchronize(old_vistrail, vistrail, 
                                                 current_action)
            vistrail = old_vistrail
    vistrail.db_last_modified = get_current_time(db_connection)

    vistrail = translate_vistrail(vistrail, vistrail.db_version, version)
    # get saved workflows from db
    workflowIds = get_saved_workflows(db_connection, vistrail.db_id)
    #print "Workflows already saved:", workflowIds
    dao_list.save_to_db(db_connection, vistrail, do_copy)
    vistrail = translate_vistrail(vistrail, version)
    vistrail.db_currentVersion = current_action

    if save_wfs:
        # update all missing tagged workflows
        tagMap = {}
        for annotation in vistrail.db_actionAnnotations:
            if annotation.db_key == '__tag__':
                tagMap[annotation.db_action_id] = annotation.db_value
        wfToSave = []
        for id, name in tagMap.iteritems():
            if id not in workflowIds:
                #print "creating workflow", vistrail.db_id, id, name,
                workflow = vistrails.db.services.vistrail.materializeWorkflow(vistrail, id)
                workflow.db_id = None
                workflow.db_vistrail_id = vistrail.db_id
                workflow.db_parent_id = id
                workflow.db_group = id
                workflow.db_last_modified=vistrail.db_get_action_by_id(id).db_date
                workflow.db_name = name
                workflow = translate_workflow(workflow, currentVersion, version)
                wfToSave.append(workflow)
                #print "done"
        if wfToSave:
            dao_list.save_many_to_db(db_connection, wfToSave, True)
    utils.commit_transaction(db_connection, trans)
    return vistrail

##############################################################################
# Workflow I/O

def open_workflow_from_xml(filename):
    """open_workflow_from_xml(filename) -> DBWorkflow"""
    return WorkflowXMLSerializer().load(filename)

def open_workflow_from_db(db_connection, id, lock=False, version=None):
    """open_workflow_from_db(db_connection, id : long: lock: bool, 
                             version: str) 
         -> DBWorkflow 
    
    """
    if db_connection is None:
        msg = "Need to call open_db_connection() before reading"
        raise VistrailsDBException(msg)
    if version is None:
        version = get_db_object_version(db_connection, id, DBWorkflow.vtType)
    dao_list = getVersionDAO(version)
    workflow = \
        dao_list.open_from_db(db_connection, DBWorkflow.vtType, id, lock)
    workflow = translate_workflow(workflow, version)
    return workflow
    
def save_workflow_to_xml(workflow, filename, version=None):

    tags = {'xmlns:xsi': 'http://www.w3.org/2001/XMLSchema-instance',
            'xsi:schemaLocation': 'http://www.vistrails.org/workflow.xsd'
            }
    if version is None:
        version = currentVersion
    if not workflow.db_version:
        workflow.db_version = currentVersion
    serializer = WorkflowXMLSerializer()
    obj = BundleObj(workflow, os.path.basename(filename))
    serializer.save(obj, os.path.dirname(filename))
    return obj

def save_workflow_bundle_to_xml(bundle, filename, version=None):
    # FIXME What is the purpose of this in the bundle system?
    if bundle.workflow is None:
        raise VistrailsDBException('save_workflow_bundle_to_xml failed, '
                                   'bundle does not contain a workflow')
    bundle.workflow = save_workflow_to_xml(bundle.workflow, filename, version)
    return bundle

def save_workflow_to_db(workflow, db_connection, do_copy=False, version=None):
    if db_connection is None:
        msg = "Need to call open_db_connection() before reading"
        raise VistrailsDBException(msg)
    if version is None:
        version = get_db_version(db_connection)
        if version is None:
            version = currentVersion
    if not workflow.db_version:
        workflow.db_version = currentVersion
    workflow = translate_workflow(workflow, workflow.db_version, version)
    dao_list = getVersionDAO(version)

    trans = db_connection.begin()
    workflow.db_last_modified = get_current_time(db_connection)
    dao_list.save_to_db(db_connection, workflow, do_copy)
    trans.commit()
    workflow = translate_workflow(workflow, version)
    return workflow

def save_workflow_bundle_to_db(save_bundle, db_connection, do_copy=False, 
                               version=None):
    if save_bundle.workflow is None:
        raise VistrailsDBException('save_workflow_bundle_to_db failed, '
                                   'bundle does not contain a workflow')
    workflow = save_workflow_to_db(save_bundle.workflow, db_connection, do_copy, 
                                   version)
    return SaveBundle(DBWorkflow.vtType, workflow=workflow)

##############################################################################
# Logging I/O

def open_log_from_xml(filename, was_appended=False):
    """open_log_from_xml(filename) -> DBLog"""
    if was_appended:
        parser = ElementTree.XMLTreeBuilder()
        parser.feed("<log>\n")
        f = open(filename, "rb")
        parser.feed(f.read())
        parser.feed("</log>\n")
        root = parser.close()
        workflow_execs = []
        for node in root:
            version = get_version_for_xml(node)
            daoList = getVersionDAO(version)
            workflow_exec = \
                daoList.read_xml_object(DBWorkflowExec.vtType, node)
            if version != currentVersion:
                # if version is wrong, dump this into a dummy log object, 
                # then translate, then get workflow_exec back
                log = DBLog()
                translate_log(log, currentVersion, version)
                log.db_add_workflow_exec(workflow_exec)
                log = translate_log(log, version)
                workflow_exec = log.db_workflow_execs[0]
            workflow_execs.append(workflow_exec)
        log = DBLog(workflow_execs=workflow_execs)
        vistrails.db.services.log.update_ids(log)
    else:
        tree = ElementTree.parse(filename)
        version = get_version_for_xml(tree.getroot())
        daoList = getVersionDAO(version)
        log = daoList.open_from_xml(filename, DBLog.vtType, tree)
        log = translate_log(log, version)
        vistrails.db.services.log.update_id_scope(log)
    return log

def open_log_from_db(db_connection, id, lock=False, version=None):
    """open_log_from_db(db_connection, id : long: lock: bool, version: str) 
         -> DBLog 
    
    """
    if db_connection is None:
        msg = "Need to call open_db_connection() before reading"
        raise VistrailsDBException(msg)
    if version is None:
        version = get_db_object_version(db_connection, id, DBLog.vtType)
    dao_list = getVersionDAO(version)
    log = dao_list.open_from_db(db_connection, DBLog.vtType, id, lock)
    log = translate_log(log, version)
    return log

def open_vt_log_from_db(db_connection, vt_id, version=None):
    """ return the logs for the specified vistrail """
    if version is None:
        version = get_db_object_version(db_connection, vt_id, DBVistrail.vtType)
    dao_list = getVersionDAO(version)
    ids = []
    if db_connection is not None:
        ids = get_db_ids_from_log(db_connection, vt_id)
    log = DBLog()
    if hasattr(dao_list, 'open_many_from_db'): # does not exist pre 1.0.2
        logs = dao_list.open_many_from_db(db_connection, DBLog.vtType, ids)
    else:
        logs = [dao_list.open_from_db(db_connection, DBLog.vtType, id) \
                for id in ids]
    for new_log in logs:
        for workflow_exec in new_log.db_workflow_execs:
            workflow_exec.db_id = log.id_scope.getNewId(DBWorkflowExec.vtType)
            log.db_add_workflow_exec(workflow_exec)
    log = translate_log(log, version)
    return log

def save_log_to_xml(log, filename, version=None, do_append=False):
    if version is None:
        version = currentVersion
    if not log.db_version:
        log.db_version = currentVersion
    log = translate_log(log, log.db_version, version)

    daoList = getVersionDAO(version)
    if do_append:
        log_file = open(filename, 'ab')
        for workflow_exec in log.db_workflow_execs:
            # cannot do correct numbering here...
            # but need to save so that we can use it for deletes
            wf_exec_id = workflow_exec.db_id
            workflow_exec.db_id = -1L
            daoList.save_to_xml(workflow_exec, log_file, {}, version)
            workflow_exec.db_id = wf_exec_id
        log_file.close()
    else:
        tags = {'xmlns:xsi': 'http://www.w3.org/2001/XMLSchema-instance',
                'xsi:schemaLocation': 'http://www.vistrails.org/log.xsd'
                }
        daoList.save_to_xml(log, filename, tags, version)
    log = translate_log(log, version)
    return log

def save_log_bundle_to_xml(save_bundle, filename, version=None):
    if save_bundle.log is None:
        raise VistrailsDBException('save_log_bundle_to_xml failed, '
                                   'bundle does not contain a log')
        
    log = save_log_to_xml(save_bundle.log, filename, version)
    return SaveBundle(DBLog.vtType, log=log)

def save_log_to_db(log, db_connection, do_copy=False, version=None):
    if db_connection is None:
        msg = "Need to call open_db_connection() before reading"
        raise VistrailsDBException(msg)
    if version is None:
        version = get_db_version(db_connection)
        if version is None:
            version = currentVersion
    if not log.db_version:
        log.db_version = currentVersion
    log = translate_log(log, log.db_version, version)
    dao_list = getVersionDAO(version)
    utils = get_sql_utils(version)

    trans = utils.start_transaction(db_connection)
    log.db_last_modified = get_current_time(db_connection)
    dao_list.save_to_db(db_connection, log, do_copy)
    utils.commit_transaction(db_connection, trans)
    log = translate_log(log, version)
    return log

def save_log_bundle_to_db(save_bundle, db_connection, do_copy=False, 
                          version=None):
    if save_bundle.log is None:
        raise VistrailsDBException('save_log_bundle_to_db failed, '
                                   'bundle does not contain a log')
        
    log = save_log_to_db(save_bundle.log, db_connection, do_copy, version)
    return SaveBundle(DBLog.vtType, log=log)

def merge_logs(new_log, vt_log_fname):
    log = open_log_from_xml(vt_log_fname, True)
    for workflow_exec in new_log.db_workflow_execs:
        workflow_exec.db_id = log.id_scope.getNewId(DBWorkflowExec.vtType)
        log.db_add_workflow_exec(workflow_exec)
    return log

##############################################################################
# OPM I/O

def save_opm_to_xml(opm_graph, filename, version=None):    
    # FIXME, we're using workflow, version, and log here...
    # which aren't in DBOpmGraph...
    if version is None:
        version = currentVersion
    daoList = getVersionDAO(version)
    tags = {'xmlns': 'http://openprovenance.org/model/v1.01.a',
            }
    opm_graph = vistrails.db.services.opm.create_opm(opm_graph.workflow, 
                                           opm_graph.version,
                                           opm_graph.log,
                                           opm_graph.registry)
    daoList.save_to_xml(opm_graph, filename, tags, version)
    return opm_graph

##############################################################################
# PROV I/O

def save_prov_to_xml(prov_document, filename, version=None):    
    # FIXME, we're using workflow, version, and log here...
    # which aren't in DBProvDocument...
    if version is None:
        version = currentVersion
    daoList = getVersionDAO(version)
    tags = {'xmlns:prov': 'http://www.w3.org/ns/prov#',
            'xmlns:dcterms': 'http://purl.org/dc/terms/',
            'xmlns:vt': 'http://www.vistrails.org/registry.xsd',
            }
    prov_document = vistrails.db.services.prov.create_prov(prov_document.workflow, 
                                                 prov_document.version,
                                                 prov_document.log)
    daoList.save_to_xml(prov_document, filename, tags, version)
    return prov_document

##############################################################################
# Registry I/O

def open_registry_from_xml(filename):
    tree = ElementTree.parse(filename)
    version = get_version_for_xml(tree.getroot())
    daoList = getVersionDAO(version)
    registry = daoList.open_from_xml(filename, DBRegistry.vtType, tree)
    if registry is None:
        raise VistrailsDBException("Couldn't read registry from XML")
    registry = translate_registry(registry, version)
    vistrails.db.services.registry.update_id_scope(registry)
    return registry

def open_registry_from_db(db_connection, id, lock=False, version=None):
    """open_registry_from_db(db_connection, id : long: lock: bool, 
                             version: str) -> DBRegistry 
    
    """
    if db_connection is None:
        msg = "Need to call open_db_connection() before reading"
        raise VistrailsDBException(msg)
    if version is None:
        version = get_db_object_version(db_connection, id, DBRegistry.vtType)
    dao_list = getVersionDAO(version)
    registry = dao_list.open_from_db(db_connection, DBRegistry.vtType, id, lock)
    registry = translate_registry(registry, version)
    return registry

def save_registry_to_xml(registry, filename, version=None):
    tags = {'xmlns:xsi': 'http://www.w3.org/2001/XMLSchema-instance',
            'xsi:schemaLocation': 'http://www.vistrails.org/registry.xsd'
            }
    if version is None:
        version = currentVersion
    if not registry.db_version:
        registry.db_version = currentVersion
    registry = translate_registry(registry, registry.db_version, version)

    daoList = getVersionDAO(version)
    daoList.save_to_xml(registry, filename, tags, version)
    registry = translate_registry(registry, version)
    return registry

def save_registry_bundle_to_xml(save_bundle, filename, version=None):
    if save_bundle.registry is None:
        raise VistrailsDBException('save_registry_bundle_to_xml failed, '
                                   'bundle does not contain a registry')
        
    registry = save_registry_to_xml(save_bundle.registry, filename, version)
    return SaveBundle(DBRegistry.vtType, registry=registry)

def save_registry_to_db(registry, db_connection, do_copy=False, version=None):
    if db_connection is None:
        msg = "Need to call open_db_connection() before reading"
        raise VistrailsDBException(msg)
    if version is None:
        version = get_db_version(db_connection)
        if version is None:
            version = currentVersion
    if not registry.db_version:
        registry.db_version = currentVersion
    registry = translate_registry(registry, registry.db_version, version)
    dao_list = getVersionDAO(version)

    trans = db_connection.begin()
    registry.db_last_modified = get_current_time(db_connection)
    dao_list.save_to_db(db_connection, registry, do_copy)
    trans.commit()
    registry = translate_registry(registry, version)
    return registry

def save_registry_bundle_to_db(save_bundle, db_connection, do_copy=False, 
                               version=None):
    if save_bundle.registry is None:
        raise VistrailsDBException('save_registry_bundle_to_db failed, '
                                   'bundle does not contain a registry')
        
    registry = save_registry_to_db(save_bundle.registry, db_connection, do_copy, 
                                   version)
    return SaveBundle(DBRegistry.vtType, registry=registry)

##############################################################################
# Abstraction I/O

def save_abstractions_to_db(abstractions, vt_id, db_connection, do_copy=False):
    """save_abstraction_to_db(abs: DBVistrail, db_connection) -> None
    Saves an abstraction to db, and updating existing abstractions

    """
    if db_connection is None:
        msg = "Need to call open_db_connection() before reading"
        raise VistrailsDBException(msg)

    for abs_name in abstractions:
        try: 
            abs = open_vistrail_from_xml(abs_name)
            abs.db_name = parse_abstraction_name(abs_name)
            id_key = '__abstraction_vistrail_id__'
            id_value = str(vt_id)
            if abs.db_has_annotation_with_key(id_key):
                annotation = abs.db_get_annotation_by_key(id_key)
                annotation.db_value = id_value
            else:
                annotation=DBAnnotation(abs.idScope.getNewId(DBAnnotation.vtType),
                                        id_key, id_value)
                abs.db_add_annotation(annotation)
            db_mod_time = None if not abs.db_id else \
                          get_db_abstraction_modification_time(db_connection, abs)

            if db_mod_time:
                delete_entity_from_db(db_connection, abs.vtType, abs.db_id)

            abs.db_id = None
            abs.db_last_modified = get_current_time(db_connection)
            version = get_db_version(db_connection)
            version = version if version else currentVersion
            if not abs.db_version:
                abs.db_version = currentVersion
            abs = translate_vistrail(abs, abs.db_version, version)
            # Always copy for now
            trans = db_connection.begin()
            getVersionDAO(version).save_to_db(db_connection, abs, True)
            trans.commit()

        except Exception, e:
            debug.critical('Could not save abstraction to db: %s' % str(e))

##############################################################################
# Thumbnail I/O

def open_thumbnails_from_db(db_connection, obj_type, obj_id, tmp_dir=None):
    """open_thumbnails_from_db(db_connection, obj_type: DB*,
                            obj_id: long, tmp_dir: str) -> [str]

    Gets a list of all thumbnails associated with this object from the
    annotations table in the db (by comparing obj_type with the column
    'entity_type' and obj_id with the column 'entity_id') and for any
    thumbnails not found in tmp_dir, they are retreived from the db and
    saved into tmp_dir.
    Returns a list of absolute file paths for all thumbnails associated
    with this object that exist in tmp_dir after the function has run.

    """
    if db_connection is None:
        msg = "Need to call open_db_connection() before reading"
        raise VistrailsDBException(msg)
    if tmp_dir is None:
        return []

    # First get associated file names from annotation table
    file_names = get_thumbnail_fnames_from_db(db_connection, obj_id, obj_type)

    # Next get all thumbnails from the db that aren't already in tmp_dir
    get_db_file_names = [fname for fname in file_names 
                         if fname not in os.listdir(tmp_dir)]
    for file_name in get_db_file_names:
        image_bytes = get_thumbnail_data_from_db(db_connection, file_name)
        if image_bytes is not None:
            absfname = os.path.join(tmp_dir, file_name)
            image_file = open(absfname, 'wb')
            image_file.write(image_bytes)
            image_file.close()
        else:
            debug.warning("db: Referenced thumbnail not found locally or "
                          "in the database: '%s'" % file_name)
    # Return only thumbnails that now exist locally
    return [os.path.join(tmp_dir, file_name) for file_name in file_names 
            if file_name in os.listdir(tmp_dir)]

def save_thumbnails_to_db(absfnames, db_connection):
    """save_thumbnails_to_db(absfnames: list, db_connection) -> None
    Saves all thumbnails from a list of local absolute file paths into the db,
    except those already present on the db.

    """
    if db_connection is None:
        msg = "Need to call open_db_connection() before reading"
        raise VistrailsDBException(msg)
    if absfnames is None or len(absfnames) == 0:
        return None

    # Determine which thumbnails already exist in db
    db_file_names = get_existing_thumbnails_in_db(db_connection, 
                        [os.path.basename(absfname) for absfname in absfnames])

    # Save any thumbnails that don't already exist in db
    insert_absfnames = [absfname for absfname in absfnames 
                        if os.path.basename(absfname) not in db_file_names]
    insert_thumbnails_into_db(db_connection, insert_absfnames)

    return None
##############################################################################
# Mashup I/O
def open_mashuptrail_from_xml(filename):
    """open_mashuptrail_from_xml(filename) -> Mashuptrail"""
    tree = ElementTree.parse(filename)
    version = get_version_for_xml(tree.getroot())
    try:
        daoList = getVersionDAO(version)
        mashuptrail = daoList.open_from_xml(filename, DBMashuptrail.vtType, tree)
        mashuptrail.currentVersion = mashuptrail.getLatestVersion()
        mashuptrail.updateIdScope()
    except VistrailsDBException, e:
        msg = "There was a problem when reading mashups from the xml file: "
        msg += str(e)
        raise VistrailsDBException(msg)
    return mashuptrail

def save_mashuptrail_to_xml(mashuptrail, filename, version=None):
    tags = {'xmlns:xsi': 'http://www.w3.org/2001/XMLSchema-instance',
            'xsi:schemaLocation': 'http://www.vistrails.org/mashup.xsd'
            }
    if version is None:
        version = currentVersion
    
    if not mashuptrail.db_version:
        mashuptrail.db_version = version
   
    #FIXME: This must be enabled at some point
    #mashuptrail = translate_mashuptrail(mashuptrail, mashuptrail.db_version, version)
    daoList = getVersionDAO(version)
    daoList.save_to_xml(mashuptrail, filename, tags, version)
    return mashuptrail

def open_mashuptrail_from_db(db_connection, mashup_id, lock=False):
    """open_mashuptrail_from_db(filename) -> Mashuptrail"""

    version = get_db_object_version(db_connection, mashup_id, DBMashuptrail.vtType)
    try:
        daoList = getVersionDAO(version)
        mashuptrail = daoList.open_from_db(db_connection, DBMashuptrail.vtType, mashup_id, lock)
        mashuptrail.currentVersion = mashuptrail.getLatestVersion()
        mashuptrail.updateIdScope()
    except VistrailsDBException, e:
        msg = "There was a problem when reading mashups from the db file: "
        msg += str(e)
        raise VistrailsDBException(msg)
    return mashuptrail

def save_mashuptrails_to_db(mashuptrails, vt_id, db_connection, do_copy=False):
    """save_mashuptrails_to_db(mashuptrails: DBMashuptrail, vt_id: int,
                               db_connection, do_copy: bool) -> None
    Saves a list of mashuptrails to the db, and replacing existing mashuptrails

    """
    if db_connection is None:
        msg = "Need to call open_db_connection() before reading"
        raise VistrailsDBException(msg)

    # for now we replace all mashups
    for old_id in get_db_mashuptrail_ids_from_vistrail(db_connection, vt_id):
        delete_entity_from_db(db_connection, Mashuptrail.vtType, old_id)

    for mashuptrail in mashuptrails:
        try: 
            id_key = '__mashuptrail_vistrail_id__'
            id_value = str(vt_id)
            if mashuptrail.db_has_annotation_with_key(id_key):
                annotation = mashuptrail.db_get_annotation_by_key(id_key)
                annotation.db_value = id_value
            else:
                annotation=DBAnnotation(mashuptrail.id_scope.getNewId(DBAnnotation.vtType),
                                        id_key, id_value)
                mashuptrail.db_add_annotation(annotation)

            # add vt_id to mashups
            for action in mashuptrail.db_actions:
                action.db_mashup.db_vtid = vt_id
            mashuptrail.db_last_modified = get_current_time(db_connection)
            mashuptrail.db_id = None
            version = get_db_version(db_connection)
            version = version if version else currentVersion
            if not mashuptrail.db_version:
                mashuptrail.db_version = currentVersion
            #FIXME: This must be enabled at some point
            #mashuptrail = translate_vistrail(mashuptrail, mashuptrail.db_version, version)
            # Always copy for now
            trans = db_connection.begin()
            getVersionDAO(version).save_to_db(db_connection, mashuptrail, True)
            trans.commit()

        except Exception, e:
            debug.critical('Could not save mashuptrail to db: %s' % str(e))

def open_startup_from_xml(filename):
    tree = ElementTree.parse(filename)
    version = get_version_for_xml(tree.getroot())
    # if version == '0.1':
    #     version = '1.0.3'
    daoList = getVersionDAO(version)
    startup = daoList.open_from_xml(filename, DBStartup.vtType, tree)
    # need this for translation...
    startup._filename = filename
    startup = translate_startup(startup, version)
    # vistrails.db.services.startup.update_id_scope(startup)
    return startup

def save_startup_to_xml(startup, filename, version=None):
    tags = {}
    if version is None:
        version = currentVersion
    if not startup.db_version:
        startup.db_version = currentVersion
    # FIXME add translation, etc.
    # startup = translate_startup(startup, startup.db_version, version)

    daoList = getVersionDAO(version)
    daoList.save_to_xml(startup, filename, tags, version)
    # startup = translate_startup(startup, version)
    return startup
    

##############################################################################
# I/O Utilities

def delete_entity_from_db(db_connection, type, obj_id):
    if db_connection is None:
        msg = "Need to call open_db_connection() before reading"
        raise VistrailsDBException(msg)
    version = get_db_version(db_connection)
    if version is None:
        version = currentVersion
    dao_list = getVersionDAO(version)
    trans = db_connection.begin()
    dao_list.delete_from_db(db_connection, type, obj_id)
    trans.commit()
    
def get_version_for_xml(root):
    version = root.get('version', None)
    if version is not None:
        return version
    msg = "Cannot find version information"
    raise VistrailsDBException(msg)

def get_type_for_xml(root):
    return root.tag

def create_temp_folder(prefix='vt_save'):
    return tempfile.mkdtemp(prefix=prefix)

def remove_temp_folder(temp_dir):
    if temp_dir is None:
        return
    if not os.path.isdir(temp_dir):
        if os.path.isfile(temp_dir):
            os.remove(temp_dir)

        # cleanup has already happened
        return
    try:
        for root, dirs, files in os.walk(temp_dir, topdown=False):
            for name in files:
                os.remove(os.path.join(root, name))
            for name in dirs:
                os.rmdir(os.path.join(root, name))
        os.rmdir(temp_dir)
    except OSError, e:
        raise VistrailsDBException("Can't remove %s: %s" % (temp_dir, str(e)))
    
##############################################################################
# Testing


class TestDBIO(unittest.TestCase):
    # def test1(self):
    #     """test importing an xml file"""

    #     vistrail = open_vistrail_from_xml( \
    #         os.path.join(vistrails.core.system.vistrails_root_directory(),
    #                      'tests/resources/dummy.xml'))
    #     assert vistrail is not None

    # def test2(self):
    #     """test importing an xml file"""

    #     vistrail = open_vistrail_from_xml( \
    #         os.path.join(vistrails.core.system.vistrails_root_directory(),
    #                      'tests/resources/dummy_new.xml'))
    #     assert vistrail is not None

    # def test3(self):
    #     """test importing a vt file"""

    #     # FIXME include abstractions
    #     (save_bundle, vt_save_dir) = open_bundle_from_zip_xml( \
    #         DBVistrail.vtType,
    #         os.path.join(vistrails.core.system.vistrails_root_directory(),
    #                      'tests/resources/dummy_new.vt'))
    #     assert save_bundle.vistrail is not None

    # def test4(self):
    #     """ test saving a vt file """

    #     # FIXME include abstractions
    #     testdir = tempfile.mkdtemp(prefix='vt_')
    #     filename = os.path.join(testdir, 'dummy_new.vt')

    #     try:
    #         (save_bundle, vt_save_dir) = open_bundle_from_zip_xml(
    #             DBVistrail.vtType,
    #             os.path.join(vistrails.core.system.vistrails_root_directory(),
    #                          'tests/resources/dummy_new.vt'))
    #         try:
    #             save_bundle_to_zip_xml(save_bundle, filename, vt_save_dir)
    #             if os.path.isfile(filename):
    #                 os.unlink(filename)
    #         except Exception, e:
    #             self.fail(str(e))
    #     finally:
    #         os.rmdir(testdir)
            
    pass

# helper function for translation tests
def get_alternate_tests(version):
    from vistrails.db.versions import get_version_path
    def test_actionAnnotations(vt1, vt2, test_class, alternate_tests):
        vt1_action_annotations = {}
        for a in vt1.db_actionAnnotations:
            a_t = (a.db_key, a.db_action_id)
            if a_t in vt1_action_annotations:
                raise AssertionError("Action annotation %s duplicated" %
                                     unicode(a_t))
            vt1_action_annotations[a_t] = a
        for a2 in vt2.db_actionAnnotations:
            a_t = (a2.db_key, a2.db_action_id)
            if a_t not in vt1_action_annotations:
                raise AssertionError("Action annotation %s not matched" % 
                                     unicode(a_t))
            a1 = vt1_action_annotations[a_t]
            a1.deep_eq_test(a2, test_class, alternate_tests)
            del vt1_action_annotations[a_t]
        if len(vt1_action_annotations) > 0:
            a_t = vt1_action_annotations.iterkeys().next()
            raise AssertionError("Action annotation %s not matched" % 
                                 unicode(a_t))

    # this works around the fact that sql backend needs to persist the
    # workflow with an id while the xml version doesn't
    def test_group_workflow(g1, g2, test_class, alternate_tests):
        self_func = alternate_tests[('DBGroup', 'db_workflow')]
        del alternate_tests[('DBGroup', 'db_workflow')]

        sql_only_fields = ['db_id', 'db_entity_type', 'db_entity_id']
        old_tests = {}
        for field in sql_only_fields:
            field_t = ('DBWorkflow', field)
            if field_t in alternate_tests:
                old_tests[field] = alternate_tests[field_t]
            alternate_tests[field_t] = None
        try:
            g1.deep_eq_test(g2, test_class, alternate_tests)
        finally:
            alternate_tests[('DBGroup', 'db_workflow')] = self_func
            for field in sql_only_fields:
                if field in old_tests:
                    alternate_tests[('DBWorkflow', field)] = old_tests[field]
                else:
                    del alternate_tests[('DBWorkflow', field)]
        
    alternate_dict = {None:
                      {('DBVistrail', 'db_entity_type'): None,
                       ('DBGroup', 'db_workflow'): test_group_workflow},
                      ('1.0.3', '1.0.4'):
                      {('DBPortSpec', 'db_depth'): None},
                      ('1.0.2', '1.0.3'): 
                      {('DBPortSpecItem', 'db_id'): None,
                       ('DBPortSpecItem', 'db_values'): None,
                       ('DBPortSpecItem', 'db_entry_type'): None,
                       ('DBPortSpec', 'db_min_conns'): None,
                       ('DBPortSpec', 'db_max_conns'): None,
                       ('DBVistrail', 'db_parameter_explorations'): None},
                      ('1.0.1', '1.0.2'):
                      {('DBActionAnnotation', 'db_date'): None,
                       ('DBActionAnnotation', 'db_user'): None,
                       ('DBActionAnnotation', 'db_id'): None,
                       ('DBVistrail', 'db_actionAnnotations'): \
                           test_actionAnnotations,
                       ('DBWorkflowExec', 'db_annotations'): None},
                      ('1.0.0', '1.0.1'):
                      {('DBPortSpecItem', 'db_default'): None,
                       ('DBPortSpecItem', 'db_label'): None},
                      ('0.9.4', '0.9.5'):
                      # FIXME should really test the module execs...
                      {('DBWorkflowExec', 'db_item_execs'): None},
                      ('0.9.3', '0.9.4'):
                      {('DBPortSpec', 'db_sort_key'): None,
                       ('DBAbstraction', 'db_namespace'): None,
                       ('DBAbstraction', 'db_package'): None,
                       ('DBAbstraction', 'db_version'): None}
                      }

    path = get_version_path(version, currentVersion)
    alternate_tests = {}
    alternate_tests.update(alternate_dict[None])
    for t in path:
        if t in alternate_dict:
            alternate_tests.update(alternate_dict[t])
    return alternate_tests

class TestXMLFile(object):
    def get_version(self):
        raise NotImplementedError("Subclass should implement get_version")

    def get_filename(self):
        from vistrails.core.system import vistrails_root_directory
        fname = os.path.join(vistrails_root_directory(), 'tests', 'resources', 
                             'test_basics.vt')
        return fname
        # return '/vistrails/src/git/examples/terminator.vt'

    def test_save_vistrail_and_reload(self):
        bundle = open_vistrail_bundle_from_zip_xml(self.get_filename())
        vt1 = bundle.vistrail

        (h, fname) = tempfile.mkstemp(prefix='vt_test_', suffix='.xml')
        os.close(h)

        try:
            save_vistrail_to_xml(vt1, fname, self.get_version())
            vt2 = open_vistrail_from_xml(fname)
            vt1.deep_eq_test(vt2, self, get_alternate_tests(self.get_version()))
        finally:
            os.unlink(fname)
            bundle.cleanup()

class TestXMLFile_v0_9_3(TestXMLFile, unittest.TestCase):
    def get_version(self):
        return '0.9.3'

class TestXMLFile_v1_0_2(TestXMLFile, unittest.TestCase):
    def get_version(self):
        return '1.0.2'

class TestSQLDatabase(object):
    conn = None

    @classmethod
    def get_config(cls):
        raise NotImplementedError

    @classmethod
    def setUpClass(cls):
        cls.conn = open_db_connection(cls.get_config())
        create_db_tables(cls.conn)

    @classmethod
    def tearDownClass(cls):
        drop_db_tables(cls.conn)
        close_db_connection(cls.conn)
        cls.conn = None

    def get_filename(self):
        from vistrails.core.system import vistrails_root_directory
        fname = os.path.join(vistrails_root_directory(), 'tests', 'resources', 
                             'test_basics.vt')
        return fname
        # return '/vistrails/src/git/examples/terminator.vt'

    def test_save_bundle(self):
        bundle = open_vistrail_bundle_from_zip_xml(self.get_filename())
        try:
            save_vistrail_bundle_to_db(bundle, self.conn, True, save_wfs=False)
        finally:
            bundle.cleanup()
        
    def test_save_vistrail_and_reload(self):
        bundle = open_vistrail_bundle_from_zip_xml(self.get_filename())
        try:
            vt1 = bundle.vistrail
            # vt1.db_version = currentVersion
            vt_id = save_vistrail_to_db(vt1, self.conn, True, 
                                        save_wfs=False).db_id
            vt1.db_id = vt_id
            vt2 = open_vistrail_from_db(self.conn, vt_id)
            vt1.deep_eq_test(vt2, self, 
                             get_alternate_tests(self.get_config()["version"]))
        finally:
            bundle.cleanup()

    # def test_z_get_db_object_list(self):
    #     print get_db_object_list(self.conn, DBVistrail.vtType)
    
    # def test_z_get_db_object_modification_time(self):
    #     print "OBJ MOD TIME:", \
    #         get_db_object_modification_time(self.conn, 1, DBVistrail.vtType)

    # def test_z_get_db_object_version(self):
    #     print "OBJ VERSION:", \
    #         get_db_object_version(self.conn, 1, DBVistrail.vtType)

    # def test_z_get_saved_workflows(self):
    #     print get_saved_workflows(self.conn, 1)
        
    # def test_z_get_db_id_from_name(self):
    #     raise Exception("Need to implement this test")

    # def test_z_get_db_abstraction_modification_time(self):
    #     raise Exception("Need to implement this test")

    # def test_z_get_db_ids_from_vistrail(self):
    #     raise Exception("Need to implement this test")

    # def test_z_get_matching_abstraction_id(self):
    #     raise Exception("Need to implement this test")

class TestMySQLDatabase(TestSQLDatabase):
    db_version = None

    @classmethod
    def get_config(cls):
        return {"user": "vt_test",
                "passwd": None,
                "host": "localhost",
                "port": None,
                "db": "vt_test",
                "version": cls.db_version}

class TestMySQLDatabase_v1_0_2(TestMySQLDatabase, unittest.TestCase):
    db_version = '1.0.2'

class TestMySQLDatabase_v1_0_3(TestMySQLDatabase, unittest.TestCase):
    db_version = '1.0.3'

class TestMySQLDatabase_v1_0_4(TestMySQLDatabase, unittest.TestCase):
    db_version = '1.0.4'

class TestMySQLDatabase_v1_0_5(TestMySQLDatabase, unittest.TestCase):
    db_version = '1.0.5'

class TestSQLite3Database(TestSQLDatabase, unittest.TestCase):
    db_fname = None

    @classmethod
    def get_db_fname(cls):
        if cls.db_fname is None:
            import os
            import tempfile
            (h, fname) = tempfile.mkstemp(prefix='vt_test_db', suffix='.db')
            os.close(h)
            cls.db_fname = fname
        return cls.db_fname

    @classmethod
    def get_config(cls):
        return {"dialect": "sqlite",
                "db": cls.get_db_fname(),
                "version": "1.0.5"}

    @classmethod
    def tearDownClass(cls):
        super(TestSQLite3Database, cls).tearDownClass()
        os.unlink(cls.db_fname)

class TestTranslations(unittest.TestCase):
    def get_filename(self):
        from vistrails.core.system import vistrails_root_directory
        fname = os.path.join(vistrails_root_directory(), 'tests', 'resources', 
                             'test_basics.vt')
        return fname
        # return '/vistrails/src/git/examples/terminator.vt'

    def run_vistrail_translation_test(self, version):
        bundle = None
        try:
            s = get_zip_bundle_serializer(self.get_filename())
            bundle = s.load()
            vt1 = bundle.vistrail
            vt2 = translate_vistrail(vt1, currentVersion, version)
            vt2 = translate_vistrail(vt2, version, currentVersion)
            vt1.deep_eq_test(vt2, self, get_alternate_tests(version))
        finally:
            if bundle is not None:
                bundle.cleanup()

    def run_workflow_translation_test(self, version):
        bundle = None
        try:
            s = get_zip_bundle_serializer(self.get_filename())
            bundle = s.load()
            vt = bundle.vistrail
            # 258 is Image Slices HW in terminator.vt
            # 20 is the executed version in test_basics.vt
            wf1 = vistrails.db.services.vistrail.materializeWorkflow(vt, 20)
            # FIXME may set db_version in materializeWorkflow?
            wf1.db_version = '1.0.5'
            wf2 = translate_workflow(wf1, currentVersion, version)
            wf2 = translate_workflow(wf2, version, currentVersion)
            wf1.deep_eq_test(wf2, self, get_alternate_tests(version))
        finally:
            if bundle is not None:
                bundle.cleanup()

    def run_log_translation_test(self, version):
        bundle = None
        try:
            s = get_zip_bundle_serializer(self.get_filename())
            bundle = s.load()
            log1 = bundle.log # lazy load here
            log1.db_version = '1.0.5'
            log2 = translate_log(log1, currentVersion, version)
            log2 = translate_log(log2, version, currentVersion)
            log1.deep_eq_test(log2, self, get_alternate_tests(version))
        finally:
            if bundle is not None:
                bundle.cleanup()

    def run_registry_translation_test(self, version):
        from vistrails.core.modules.module_registry import get_module_registry

        (h, fname) = tempfile.mkstemp(prefix='vt_test_', suffix='.xml')
        os.close(h)
        try:
            out_fname = save_registry_to_xml(get_module_registry(), fname)
            reg1 = open_registry_from_xml(fname)
            reg2 = translate_registry(reg1, currentVersion, version)
            reg2 = translate_registry(reg2, version, currentVersion)
            reg1.deep_eq_test(reg2, self, get_alternate_tests(version))
        finally:
            os.unlink(fname)

    def test_v0_9_3_vistrail(self):
        self.run_vistrail_translation_test('0.9.3')

    def test_v0_9_3_workflow(self):
        self.run_workflow_translation_test('0.9.3')

    def test_v0_9_5_log(self):
        self.run_log_translation_test('0.9.5')

    def test_v0_9_3_log(self):
        self.run_log_translation_test('0.9.3')

    # registry was introduced in 0.9.5 so cannot test back to 0.9.3
    def test_v0_9_5_registry(self):
        self.run_registry_translation_test('0.9.5')

    def test_v1_0_1_vistrail(self):
        self.run_vistrail_translation_test('1.0.1')

    def test_v1_0_1_workflow(self):
        self.run_workflow_translation_test('1.0.1')

    def test_v1_0_1_log(self):
        self.run_log_translation_test('1.0.1')

    def test_v1_0_1_registry(self):
        self.run_registry_translation_test('1.0.1')

    def test_new_bundle(self):
        s1 = None
        s2 = None
        s3 = None
        s4 = None
        (h1, fname1) = tempfile.mkstemp(prefix='vt_test_', suffix='.zip')
        os.close(h1)
        (h2, fname2) = tempfile.mkstemp(prefix='vt_test_', suffix='.zip')
        os.close(h2)
        try:
            s1 = get_zip_bundle_serializer(self.get_filename())
            bundle = s1.load()
            s2 = get_zip_bundle_serializer(bundle=bundle)
            s2.save(fname1)

            saved_zip1 = zipfile.ZipFile(fname1)
            paths = saved_zip1.namelist()
            self.assertEqual(len(paths), 4)
            self.assertTrue('MANIFEST' in paths)
            self.assertTrue('log' in paths)
            self.assertTrue('vistrail' in paths)
            self.assertTrue('subworkflows/AddValues(29ff781c-fef5-11e2-abf5-0023dfde3d57).xml' in paths)

            s3 = get_zip_bundle_serializer(fname=fname1)
            bundle2 = s3.load()
            s4 = get_zip_bundle_serializer(bundle=bundle2, version='1.0.3')
            s4.save(fname2)

            saved_zip2 = zipfile.ZipFile(fname2)
            paths2 = saved_zip2.namelist()
            self.assertEqual(len(paths2), 3)
            self.assertFalse('MANIFEST' in paths2)
            self.assertTrue('log' in paths2)
            self.assertTrue('vistrail' in paths2)
            self.assertTrue('abstraction_AddValues(29ff781c-fef5-11e2-abf5-0023dfde3d57).xml' in paths2)
        finally:
            if s1 is not None:
                s1.cleanup()
            if s2 is not None:
                s2.cleanup()
            if s3 is not None:
                s3.cleanup()
            os.unlink(fname1)
            os.unlink(fname2)
        
if __name__ == '__main__':
    import vistrails.core.application
    vistrails.core.application.init()
    unittest.main()
