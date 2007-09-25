# MACOSX binary install stuff
import os
os.environ['EXECUTABLEPATH'] = '/vistrails/VisTrails.app/Contents/MacOS'

from db.services import io

def convert_xml_to_sql(filename):
    config = {'host': 'localhost', 
              'port': 3306,
              'user': 'vistrails',
              'passwd': 'vistrailspwd',
              'db': 'vistrails'}

    try:
        vistrail = io.open_vistrail_from_xml(filename)
        dbConnection = io.open_db_connection(config)

        print dbConnection.get_server_info()
        print dbConnection.get_host_info()
        print dbConnection.stat()
        print str(dbConnection)

        io.save_vistrail_to_db(vistrail, dbConnection)
        io.close_db_connection(dbConnection)
        print 'db_id: ', vistrail.db_id

    except Exception, e:
        print e

if __name__ == '__main__':
    # convert_xml_to_sql('/vistrails/vtk_http_new.xml')
    convert_xml_to_sql('/vistrails/examples/head.xml')
    # convert_xml_to_sql('/vistrails/examples/lung.xml')
