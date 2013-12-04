from IPython.parallel.error import RemoteError

from vistrails.core.parallelization import SchemeType, Parallelization, \
    ParallelizationScheme
from vistrails.core.parallelization.common import get_pickled_module_inputs, \
    execute_serialized_pipeline, module_to_serialized_pipeline, set_results
from vistrails.core.modules.vistrails_module.errors import ModuleError

from .api import get_client
from .engine_manager import EngineManager
from .utils import print_remoteerror


def initialize_then_execute_serialized_pipeline(*args):
    global init
    global app

    try:
        init
    except NameError:
        init = False

    if not init:
        import vistrails.core.application

        # Start a VisTrails application
        app = vistrails.core.application.init({'spawned': True}, args=[])

        init = True

    return execute_serialized_pipeline(*args)


@apply
class IPythonScheme(ParallelizationScheme):
    @staticmethod
    def get_gui_widget():
        from vistrails.gui.parallelization.parallel_ipython import \
            QParallelIPythonSettings
        return QParallelIPythonSettings

    def __init__(self):
        ParallelizationScheme.__init__(self,
                50, # rather high priority; if the user connected to an IPython
                    # cluster, he probably wants to use it
                SchemeType.REMOTE_MACHINE,
                'ipython')
        self._enabled = False

    def do_compute(self, module):
        # Connect to cluster
        try:
            rc = get_client(shared_client=True)
        except Exception, error:
            raise ModuleError(
                    module,
                    "Exception while loading IPython: %s" % error)
        if not rc.ids:
            raise ModuleError(
                    module,
                    "Exception while loading IPython: No IPython engines "
                    "detected!")

        # Build the pipeline
        inputs = get_pickled_module_inputs(module)
        pipeline, moduleId, connected_outputports = \
                module_to_serialized_pipeline(module)

        # Start execution
        with rc.load_balanced_view() as ldview:
            future = ldview.apply_async(
                    initialize_then_execute_serialized_pipeline,
                    pipeline,
                    moduleId,
                    inputs,
                    connected_outputports)
        async_task = module._runner.make_async_task()

        # IPython currently doesn't have a callback mechanism, so we start a
        # thread that blocks on AsyncResult#get()
        # Moreover, ZeroMQ sockets are NOT thread-safe, meaning that we cannot
        # just start a thread here to wait on the AsyncResult, as it would be
        # invalid to perform any other operation on the Client while it is
        # pending
        def callback(res):
            def get_results():
                def compute():
                    try:
                        set_results(module, res.get())
                    except RemoteError, e:
                        print_remoteerror(e)
                        raise
                module.do_compute(compute=compute)
            async_task.callback(get_results)
        rc.add_callback(future, callback)

    def enabled(self):
        return self._enabled

    def set_enabled(self, enabled):
        self._enabled = enabled

    def finalize(self):
        EngineManager.cleanup()


Parallelization.register_parallelization_scheme(IPythonScheme)
