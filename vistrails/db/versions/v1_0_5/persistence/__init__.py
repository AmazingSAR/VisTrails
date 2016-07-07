###############################################################################
##
## Copyright (C) 2011-2013, NYU-Poly.
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
##  - Neither the name of the University of Utah nor the names of its 
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

# the fact that "xml" is a submodule here can cause issues so we
# remove the current directory from the path before continuing
if __name__ == '__main__':
    import sys
    # assume current directory is first on the path
    del sys.path[0]

from itertools import izip

from vistrails.db.versions.v1_0_5.persistence.xml.auto_gen import XMLDAOListBase
from vistrails.db.versions.v1_0_5.persistence.sql.auto_gen import SQLDAOListBase
from vistrails.db.versions.v1_0_5.persistence.sql import alchemy

from vistrails.core.system import get_elementtree_library, vistrails_root_directory
from vistrails.db import VistrailsDBException
from vistrails.db.services.bundle import Bundle, BundleObj, BundleMapping, \
    XMLFileSerializer, XMLAppendSerializer,FileRefSerializer, \
    DirectorySerializer, DirectoryBaseSerializer, ZIPBaseSerializer, \
    SingleRootBundleObjMapping, MultipleObjMapping, MultipleFileRefMapping
from vistrails.db.versions.v1_0_5 import version as my_version
from vistrails.db.versions.v1_0_5.domain import DBGroup, DBWorkflow, DBVistrail, DBLog, \
    DBRegistry, DBMashuptrail, DBWorkflowExec

root_set = set([DBVistrail.vtType, DBWorkflow.vtType, 
                DBLog.vtType, DBRegistry.vtType, DBMashuptrail.vtType])

ElementTree = get_elementtree_library()

vistrail_bmap = BundleMapping(my_version, 'vistrail',
                              [SingleRootBundleObjMapping(
                                  DBVistrail.vtType, 'vistrail'),
                                  SingleRootBundleObjMapping(DBLog.vtType,
                                                             'log'),
                                  SingleRootBundleObjMapping(
                                      DBRegistry.vtType, 'registry'),
                                  MultipleObjMapping(DBMashuptrail.vtType,
                                                     lambda obj: obj.db_name,
                                                     'mashup'),
                                  MultipleFileRefMapping('thumbnail'),
                                  MultipleFileRefMapping('abstraction'),
                                  SingleRootBundleObjMapping('job'),
                                  MultipleFileRefMapping('data'),
                              ],
                              primary_obj_type='vistrail')
workflow_bmap = vistrail_bmap.clone(primary_obj_type='workflow')
workflow_bmap.remove_mapping('vistrail')
workflow_bmap.add_mapping(
    SingleRootBundleObjMapping(DBWorkflow.vtType, 'workflow'))

class LogXMLSerializer(XMLAppendSerializer):
    def __init__(self, mapping):
        XMLAppendSerializer.__init__(self, mapping,
                                     "http://www.vistrails.org/log.xsd",
                                     "translateLog",
                                     DBWorkflowExec.vtType,
                                     'log',
                                     True, True)

    def create_obj(self, inner_obj_list=None):
        if inner_obj_list is not None:
            return DBLog(workflow_execs=inner_obj_list)
        else:
            return DBLog()

    def get_inner_objs(self, vt_obj):
        return vt_obj.db_workflow_execs

    def add_inner_obj(self, vt_obj, inner_obj):
        vt_obj.db_add_workflow_exec(inner_obj)


class MashupXMLSerializer(XMLFileSerializer):
    def __init__(self, mapping):
        XMLFileSerializer.__init__(self, mapping,
                                   "http://www.vistrails.org/mashup.xsd",
                                   "translateMashup",
                                   inner_dir_name="mashups")

    def finish_load(self, b_obj):
        b_obj.obj_type = "mashup"

    def get_obj_id(self, b_obj):
        return b_obj.obj.db_name


base_dir_serializer = DirectoryBaseSerializer()
vt_dir_serializer = DirectorySerializer(vistrail_bmap,
                                        [XMLFileSerializer(
                                            vistrail_bmap.get_mapping("vistrail"),
                                            "http://www.vistrails.org/vistrail.xsd",
                                            "translateVistrail",
                                            True, True),
                                        (LogXMLSerializer(
                                                vistrail_bmap.get_mapping("log")), True),
                                        MashupXMLSerializer(
                                                vistrail_bmap.get_mapping(
                                                    "mashuptrail")),
                                        FileRefSerializer(
                                            vistrail_bmap.get_mapping(
                                                'abstraction'), 'subworkflows')])
wf_dir_serializer = DirectorySerializer(workflow_bmap,
                                        [XMLFileSerializer(
                                            workflow_bmap.get_mapping("workflow"),
                                            "http://www.vistrails.org/workflow.xsd",
                                            "translateWorkflow",
                                            True, True),
                                            (LogXMLSerializer(
                                                workflow_bmap.get_mapping("log")), True),
                                            MashupXMLSerializer(
                                                workflow_bmap.get_mapping(
                                                    "mashuptrail")),
                                            FileRefSerializer(
                                                workflow_bmap.get_mapping(
                                                    'abstraction'),
                                                'subworkflows')])
# add_file_serializers(bmap, vt_dir_serializer)
base_dir_serializer.register_serializer(vt_dir_serializer)
base_dir_serializer.register_serializer(wf_dir_serializer)
base_zip_serializer = ZIPBaseSerializer()
base_zip_serializer.copy_serializers(base_dir_serializer)

class DAOList(dict):
    def __init__(self):
        self['xml'] = XMLDAOListBase()
        self['sql'] = SQLDAOListBase()

    def parse_xml_file(self, filename):
        return ElementTree.parse(filename)

    def write_xml_file(self, filename, tree):
        def indent(elem, level=0):
            i = "\n" + level*"  "
            if len(elem):
                if not elem.text or not elem.text.strip():
                    elem.text = i + "  "
                if not elem.tail or not elem.tail.strip():
                    elem.tail = i
                for elem in elem:
                    indent(elem, level+1)
                if not elem.tail or not elem.tail.strip():
                    elem.tail = i
            else:
                if level and (not elem.tail or not elem.tail.strip()):
                    elem.tail = i
        indent(tree.getroot())
        tree.write(filename)

    def read_xml_object(self, vtType, node):
        return self['xml'][vtType].fromXML(node)

    def write_xml_object(self, obj, node=None):
        res_node = self['xml'][obj.vtType].toXML(obj, node)
        return res_node
        
    def open_from_xml(self, filename, vtType, tree=None):
        """open_from_xml(filename) -> DBVistrail"""
        if tree is None:
            tree = self.parse_xml_file(filename)
        vistrail = self.read_xml_object(vtType, tree.getroot())
        return vistrail

    def save_to_xml(self, obj, filename, tags, version=None):
        """save_to_xml(obj : object, filename: str, tags: dict,
                       version: str) -> None
    
        """
        root = self.write_xml_object(obj)
        if version is None:
            version = my_version
        root.set('version', version)
        for k, v in tags.iteritems():
            root.set(k, v)
        tree = ElementTree.ElementTree(root)
        self.write_xml_file(filename, tree)

    def open_from_db(self, db_connection, vtType, id=None, lock=False, 
                     global_props=None):
        results = self.open_many_from_db(db_connection, vtType, [id], lock,
                                         [global_props])
        return results[0]

    def open_many_from_db(self, db_connection, vtType, ids, lock=False,
                          global_props_list=None):
        """ Loads multiple objects. They need to be loaded as one single
            multiple select statement command for performance reasons.
        """

        dao = self['sql'][vtType]
        
        if global_props_list is None:
            global_props_list = [None,] * len(ids)

        # loop through ids and build SELECT statements
        selects = []
        for id, global_props in izip(ids, global_props_list):

            if global_props is None:
                global_props = {}
            if id is not None:
                global_props['id'] = id
            select = dao.get_sql_select(db_connection, global_props, lock)
            selects.append(select)

        # Execute all SELECT statements for main objects
        results = dao.executeSQLCommands(db_connection, selects, True)

        # list of final objects
        objects = []
        # list of selects
        selects = []
        # list of children id:all_objects_dict
        all_objects_dict = {}
        # process each result and extract child SELECTS
        # daoList should contain (id, dao_type, dao, result) values
        daoList = []
        # selects should contain dbCommand values
        selects = []
        global_props_dict = {}
        for id, data in izip(ids, results):
            res_objects = dao.process_sql_columns(data, global_props)
            if len(res_objects) > 1:
                raise VistrailsDBException("More than object of type '%s' and "
                                           "id '%s' exist in the database" % \
                                               (vtType, id))
            elif len(res_objects) <= 0:
                raise VistrailsDBException("No objects of type '%s' and "
                                           "id '%s' exist in the database" % \
                                               (vtType, id))
            all_objects = {}
            all_objects_dict[id] = all_objects
            all_objects.update(res_objects)
            res = res_objects.values()[0]
            objects.append(res)
            global_props = {'entity_id': res.db_id,
                            'entity_type': res.vtType}
            global_props_dict[id] = global_props
            # collect all commands so that they can be executed together
        
            # generate SELECT statements for children
            for dao_type, dao in self['sql'].iteritems():
                if dao_type in root_set:
                    continue
    
                daoList.append((id, dao_type, dao))
                dbCommand = dao.get_sql_select(db_connection, global_props, lock)
                selects.append(dbCommand)

        # Execute all child select statements
        results = self['sql'][vtType].executeSQLCommands(db_connection,
                                                      selects, True)

        # process results
        for (id, dao_type, dao), data in izip(daoList, results):                
            all_objects = all_objects_dict[id]
            current_objs = dao.process_sql_columns(data, global_props_dict[id])
            all_objects.update(current_objs)

            if dao_type == DBGroup.vtType:
                for key, obj in current_objs.iteritems():
                    new_props = {'parent_id': key[1],
                                 'entity_id': global_props_dict[id]['entity_id'],
                                 'entity_type': global_props_dict[id]['entity_type']}
                    res_obj = self.open_from_db(db_connection, 
                                                DBWorkflow.vtType, 
                                                None, lock, new_props)
                    res_dict = {}
                    res_dict[(res_obj.vtType, res_obj.db_id)] = res_obj
                    all_objects.update(res_dict)

        
        for id, all_objects in all_objects_dict.iteritems():
            for key, obj in all_objects.iteritems():
                if key[0] == vtType and key[1] == id:
                    continue
                self['sql'][obj.vtType].from_sql_fast(obj, all_objects)
        for all_objects in all_objects_dict.itervalues():
            for obj in all_objects.itervalues():
                obj.is_dirty = False
                obj.is_new = False
    
        return objects

    def save_to_db(self, db_connection, obj, do_copy=False, global_props=None):
        if global_props is None:
            global_props = {'entity_type': obj.vtType}
        self.save_many_to_db(db_connection, [obj], do_copy, [global_props])

    def save_many_to_db(self, db_connection, objList, do_copy=False, 
                        global_props_list=None):
        if len(objList) < 1:
            return

        if global_props_list is None:
            global_props_list = [{'entity_type': obj.vtType}
                                 for obj in objList]
        if do_copy == 'with_ids':
            do_copy = True

        dbCommandList = []
        obj_children = []
        obj_written = []
        for obj, global_props in izip(objList, global_props_list):
            if do_copy and obj.db_id is not None:
                obj.db_id = None

            children = obj.db_children()
            children.reverse()

            child = children[0][0]
            dbCommand = self['sql'][child.vtType].set_sql_command(
                db_connection, child, global_props, do_copy)
            if dbCommand is not None:
                dbCommandList.append(dbCommand)
                obj_written.append(True)
            else:
                obj_written.append(False)

            obj_children.append(children)

        # Execute all insert/update statements for the main objects
        results = self['sql'][children[0][0].vtType].executeSQLCommands(
                                                    db_connection,
                                                    dbCommandList, False)

        result_idx = 0
        dbCommandList = []
        sub_obj_written = []
        for children, global_props, was_written in \
            izip(obj_children, global_props_list, obj_written):

            child = children[0][0]
            if was_written:
                self['sql'][child.vtType].set_sql_process(
                    child, global_props, results[result_idx])
                result_idx += 1
            self['sql'][child.vtType].to_sql_fast(child, do_copy)

            # process children
            global_props = {'entity_id': child.db_id,
                            'entity_type': child.vtType}
            
            # do deletes
            if not do_copy:
                for (child, _, _) in children:
                    for c in child.db_deleted_children(True):
                        self['sql'][c.vtType].delete_sql_column(db_connection,
                                                                c,
                                                                global_props)
            child = children.pop(0)[0]
            child.is_dirty = False
            child.is_new = False
            
            # list of all children
            # process remaining children
            subchildren_written = []
            for (child, _, _) in children:
                dbCommand = self['sql'][child.vtType].set_sql_command(
                                db_connection, child, global_props, do_copy)
                if dbCommand is not None:
                    dbCommandList.append(dbCommand)
                    subchildren_written.append(True)
                else:
                    subchildren_written.append(False)
                self['sql'][child.vtType].to_sql_fast(child, do_copy)
            sub_obj_written.append(subchildren_written)

        # Execute all child insert/update statements
        results = self['sql'][children[0][0].vtType].executeSQLCommands(
                                                        db_connection,
                                                        dbCommandList, False)

        obj_written = sub_obj_written
        result_idx = 0
        for children, global_props, sub_was_written in \
            izip(obj_children, global_props_list, obj_written):
            for (child, _, _), was_written in izip(children, sub_was_written):
                if was_written:
                    self['sql'][child.vtType].set_sql_process(
                        child, global_props, results[result_idx])
                    result_idx += 1

                self['sql'][child.vtType].to_sql_fast(child, do_copy)
                if child.vtType == DBGroup.vtType:
                    if child.db_workflow:
                        new_props = {'entity_id': global_props['entity_id'],
                                     'entity_type': global_props['entity_type']}
                        is_dirty = child.db_workflow.is_dirty
                        child.db_workflow.db_entity_type = DBWorkflow.vtType
                        child.db_workflow.is_dirty = is_dirty
                        self.save_to_db(db_connection, child.db_workflow, 
                                        do_copy, new_props)

    def delete_from_db(self, db_connection, type, obj_id):
        if type not in root_set:
            raise VistrailsDBException("Cannot delete entity of type '%s'" \
                                           % type)

        id_str = str(obj_id)
        for (dao_type, dao) in self['sql'].iteritems():
            if dao_type not in root_set:
                db_cmd = \
                    self['sql'][type].createSQLDelete(dao.table,
                                                      {'entity_type': type,
                                                       'entity_id': id_str})
                self['sql'][type].executeSQL(db_connection, db_cmd, False)
        db_cmd = self['sql'][type].createSQLDelete(self['sql'][type].table,
                                                   {'id': id_str})
        self['sql'][type].executeSQL(db_connection, db_cmd, False)

    def serialize(self, object):
        root = self.write_xml_object(object)
        return ElementTree.tostring(root)

    def unserialize(self, str, obj_type):
        def set_dirty(obj):
            for child, _, _ in obj.db_children():
                if child.vtType == DBGroup.vtType:
                    if child.db_workflow:
                        set_dirty(child.db_workflow)
                child.is_dirty = True
                child.is_new = True
        try:
            root = ElementTree.fromstring(str)
            obj = self.read_xml_object(obj_type, root)
            set_dirty(obj)
            return obj
        except SyntaxError, e:
            msg = "Invalid VisTrails serialized object %s" % str
            raise VistrailsDBException(msg)
            return None

import unittest
import difflib
import os
import sys
import shutil
import tempfile

class TestPersistence(unittest.TestCase):

    def run_sql_save_vistrail(self, test_db):
        from vistrails.db.domain import DBVistrail
        from vistrails.db.versions.v1_0_5.persistence.sql.sql_dao import SQLDAO
        import sqlalchemy
        dao_list = DAOList()

        SQLDAO.engine = sqlalchemy.create_engine(test_db)
        SQLDAO.metadata.create_all(SQLDAO.engine)

        in_fname = os.path.join(vistrails_root_directory(),
                                'tests/resources/terminator-vt.xml')
        vt1 = dao_list.open_from_xml(in_fname, DBVistrail.vtType)
        conn = SQLDAO.engine.connect()
        trans = conn.begin()
        dao_list.save_to_db(conn, vt1, True)
        trans.commit()
        vt2 = dao_list.open_from_db(conn, DBVistrail.vtType, 
                                    id=vt1.db_id)
        vt2.db_actions.sort(key=lambda x: x.db_id)
        for a in vt2.db_actions:
            a.db_operations.sort(key=lambda x: x.db_id)
            a.db_annotations.sort(key=lambda x: x.db_id)
        vt2.db_actionAnnotations.sort(key=lambda x: x.db_id)

        (h, out_fname) = tempfile.mkstemp(prefix='vt_test_', suffix='.xml')
        os.close(h)
        dao_list.save_to_xml(vt2, out_fname, {})

        in_lines = open(in_fname, 'U').readlines()
        out_lines = open(out_fname, 'U').readlines()

        diff = difflib.unified_diff(in_lines, out_lines, in_fname, out_fname)
        sys.stdout.writelines(diff)

        # cleanup

        os.unlink(out_fname)

        trans = conn.begin()
        SQLDAO.metadata.drop_all(conn)
        trans.commit()

    def test_save_vistrail_mysql(self):
        test_db = 'mysql+mysqldb://vt_test@localhost/vt_test'
        self.run_sql_save_vistrail(test_db)

    def test_save_vistrail_sqlite3(self):
        import os
        import tempfile
        (h, fname) = tempfile.mkstemp(prefix='vt_test_db', suffix='.db')
        os.close(h)
        test_db = 'sqlite:///%s' % fname
        try:
            self.run_sql_save_vistrail(test_db)
        finally:
            os.unlink(fname)

    def compare_bundles(self, b1, b2):
        self.assertEqual(len(b1.get_items()), len(b2.get_items()))
        for obj_type, obj_id, obj in b1.get_items():
            obj2 = b2.get_bundle_obj(obj_type, obj_id)
            # not ideal, but fails when trying to compare objs without __eq__
            self.assertEqual(obj.obj.__class__, obj2.obj.__class__)
            # self.assertEqual(str(obj.obj), str(obj2.obj))

    def create_vt_bundle(self):
        from vistrails.core.system import resource_directory
        b = Bundle(vistrail_bmap)
        b.add_object(DBVistrail())
        b.add_object(DBLog())
        fname1 = os.path.join(resource_directory(), 'images', 'info.png')
        b.add_object(fname1, 'thumbnail') # info.png
        fname2 = os.path.join(resource_directory(), 'images', 'left.png')
        b.add_object(fname2, 'thumbnail') # left.png
        return b

    def test_vt_dir_bundle(self):
        d = tempfile.mkdtemp(prefix='vtbundle_test')
        inner_d = os.path.join(d, 'mybundle')

        s = base_dir_serializer
        b1 = None
        b2 = None
        try:
            b1 = self.create_vt_bundle()
            s.save(b1, inner_d)

            b2 = s.load(inner_d)

            self.compare_bundles(b1, b2)
        finally:
            if b1:
                b1.cleanup()
            if b2:
                b2.cleanup()
            shutil.rmtree(d)

if __name__ == '__main__':
    unittest.main()
