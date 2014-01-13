from vistrails.core.bundles.pyimport import py_import
from vistrails.core.modules.utils import make_modules_dict
from vistrails.core.packagemanager import get_package_manager
from vistrails.core.upgradeworkflow import UpgradeWorkflowHandler

try:
    py_import('numpy', {
            'pip': 'numpy',
            'linux-debian': 'python-numpy',
            'linux-ubuntu': 'python-numpy',
            'linux-fedora': 'numpy'})
except ImportError:
    pass

from .common import _modules as common_modules
from .convert import _modules as convert_modules
from .read import _modules as read_modules
from .write import _modules as write_modules


_modules = [common_modules,
            convert_modules,
            read_modules,
            write_modules]

if get_package_manager().has_package('org.vistrails.vistrails.spreadsheet'):
    from .viewer import _modules as viewer_modules
    _modules.append(viewer_modules)

_modules = make_modules_dict(*_modules)


def handle_module_upgrade_request(controller, module_id, pipeline):
    module_remap = {
            'read|csv|CSVFile': [
                (None, '0.1.1', 'read|CSVFile', {})
            ],
            'read|numpy|NumPyArray': [
                (None, '0.1.1', 'read|NumPyArray', {})
            ],
        }

    return UpgradeWorkflowHandler.remap_module(controller,
                                               module_id,
                                               pipeline,
                                               module_remap)
