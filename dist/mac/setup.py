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
"""
This is a setup.py script generated by py2applet

Usage:
    python setup.py py2app
"""

from setuptools import setup
import sys

VERSION = '2.1beta2'

plist = dict(
    CFBundleName='VisTrails',
    CFBundleShortVersionString=VERSION,
    CFBundleGetInfoString=' '.join(['VisTrails', VERSION]),
    CFBundleExecutable='vistrails',
    CFBundleIdentifier='org.vistrails',
)

sys.path.append('../..')
APP = ['../../vistrails/run.py']
#comma-separated list of additional data files and
#folders to include (not for code!)
#DATA_FILES = ['/usr/local/graphviz-2.12/bin/dot',]
#removed gridifield: gridfield, gridfield.core, gridfield.algebra, gridfield.gfvis, gridfield.selfe, \
OPTIONS = {'argv_emulation': True,
           'iconfile': 'resources/vistrails_icon.icns',
           'includes': 'sip,pylab,xml,\
            libxml2,libxslt, Cookie, BaseHTTPServer, multifile, \
                        shelve, uuid, \
                        sine,st,Numeric,pexpect,psycopg2,sqlite3,suds,shapelib, dbflib,mpl_toolkits.mplot3d,_mysql_exceptions',
           'packages': 'PyQt4,vtk,MySQLdb,matplotlib,vistrails,numpy,scipy,api,twisted,Scientific,distutils,h5py,batchq,osgeo,nose,IPython,readline,pyzmq',
           'excludes': 'mpl_toolkits.basemap,PyQt4.uic,PyQt4.uic.Compiler,PyQt4.uic.Loader,PyQt4.uic.port_v2,PyQt4.uic.port_v3',
           'plist': plist,
           }

setup(
    app=APP,
 #   data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
