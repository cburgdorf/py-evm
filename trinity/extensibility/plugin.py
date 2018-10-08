from abc import (
    ABC,
    abstractmethod
)
from argparse import (
    ArgumentParser,
    Namespace,
    _SubParsersAction,
)
import asyncio
import logging
from multiprocessing import (
    Process
)
from typing import (
    Any,
    Dict,
)

from lahja import (
    BroadcastConfig,
    Endpoint,
)

from trinity.config import (
    TrinityConfig
)
from trinity.constants import (
    MAIN_EVENTBUS_ENDPOINT
)
from trinity.events import (
    ShutdownRequest,
)
from trinity.extensibility.events import (
    BaseEvent,
    PluginStartedEvent,
)
from trinity.extensibility.exceptions import (
    EventBusNotReady,
)
from trinity.utils.ipc import (
    kill_process_gracefully
)
from trinity.utils.mp import (
    ctx,
)
from trinity.utils.logging import (
    setup_queue_logging,
)


class PluginContext:
    """
    The ``PluginContext`` holds valuable contextual information such as the parsed
    arguments that were used to launch ``Trinity``. It also provides access to APIs
    such as the ``EventBus``.

    Each plugin gets a ``PluginContext`` injected during startup.
    """

    def __init__(self, endpoint: Endpoint) -> None:
        self.event_bus = endpoint
        self.boot_kwargs: Dict[str, Any] = None
        self.args: Namespace = None
        self.trinity_config: TrinityConfig = None

    def shutdown_host(self) -> None:
        self.event_bus.broadcast(
            ShutdownRequest(),
            BroadcastConfig(filter_endpoint=MAIN_EVENTBUS_ENDPOINT)
        )


class BasePlugin(ABC):

    context: PluginContext = None
    running: bool = False

    @property
    @abstractmethod
    def name(self) -> str:
        """
        Describe the name of the plugin
        """
        raise NotImplementedError(
            "Must be implemented by subclasses"
        )

    @property
    def logger(self) -> logging.Logger:
        return logging.getLogger('trinity.extensibility.plugin.BasePlugin#{0}'.format(self.name))

    @property
    def event_bus(self) -> Endpoint:
        if self.context is None:
            raise EventBusNotReady("Tried accessing ``event_bus`` before ``ready`` was called")

        return self.context.event_bus

    def set_context(self, context: PluginContext) -> None:
        """
        Set the :class:`~trinity.extensibility.plugin.PluginContext` for this plugin.
        """
        self.context = context

    def ready(self) -> None:
        """
        Called after the plugin received its context and is ready to bootstrap itself.
        """
        pass

    def configure_parser(self, arg_parser: ArgumentParser, subparser: _SubParsersAction) -> None:
        """
        Called at startup, giving the plugin a chance to amend the Trinity CLI argument parser
        """
        pass

    def boot(self) -> None:
        """
        Prepare the plugin to get started and eventually cause ``start`` to get called.
        """
        self.running = True
        self.start()
        self.event_bus.broadcast(
            PluginStartedEvent(type(self))
        )
        self.logger.info("Plugin started: %s", self.name)

    def start(self) -> None:
        """
        The ``start`` method is called only once when the plugin is started. In the case
        of an `BaseIsolatedPlugin` this method will be launched in a separate process.
        """
        pass


class BaseSyncStopPlugin(BasePlugin):
    """
    A ``BaseSyncStopPlugin`` unwinds synchronoulsy, hence blocks until shut down is done.
    """
    def stop(self) -> None:
        pass


class BaseAsyncStopPlugin(BasePlugin):
    """
    A ``BaseAsyncStopPlugin`` unwinds asynchronoulsy, hence needs to be awaited.
    """

    async def stop(self) -> None:
        pass


class BaseMainProcessPlugin(BasePlugin):
    """
    A ``BaseMainProcessPlugin`` overtakes the whole main process before most of the Trinity boot
    process had a chance to start. In that sense it redefines the whole meaning of the ``trinity``
    process.
    """
    pass


class BaseIsolatedPlugin(BaseSyncStopPlugin):
    """
    A ``BaseIsolatedPlugin`` runs in an isolated process and doesn't dictate whether its
    implementation is based on non-blocking asyncio or synchronous calls. When an isolated
    plugin is stopped it will first receive a SIGINT followed by a SIGTERM soon after.
    It is up to the plugin to handle these signals accordingly.
    """

    _process: Process = None

    def boot(self) -> None:
        """
        Prepare the plugin to get started and eventually cause ``start`` to get called.
        """
        self.running = True
        self._process = ctx.Process(
            target=self._prepare_start,
        )

        self._process.start()
        self.logger.info("Plugin started: %s", self.name)

    def _prepare_start(self) -> None:
        log_queue = self.context.boot_kwargs['log_queue']
        level = self.context.boot_kwargs.get('log_level', logging.INFO)
        setup_queue_logging(log_queue, level)
        self.event_bus.connect_no_wait()
        self.event_bus.broadcast(
            PluginStartedEvent(type(self))
        )
        self.start()

    def stop(self) -> None:
        self.context.event_bus.stop()
        kill_process_gracefully(self._process, self.logger)


class DebugPlugin(BaseAsyncStopPlugin):
    """
    This is a dummy plugin useful for demonstration and debugging purposes
    """

    @property
    def name(self) -> str:
        return "Debug Plugin"

    def configure_parser(self, arg_parser: ArgumentParser, subparser: _SubParsersAction) -> None:
        arg_parser.add_argument("--debug-plugin", type=bool, required=False)

    def handle_event(self, activation_event: BaseEvent) -> None:
        self.logger.info("Debug plugin: handle_event called: %s", activation_event)

    def start(self) -> None:
        self.logger.info("Debug plugin: start called")
        asyncio.ensure_future(self.count_forever())

    async def count_forever(self) -> None:
        i = 0
        while True:
            self.logger.info(i)
            i += 1
            await asyncio.sleep(1)

    async def stop(self) -> None:
        self.logger.info("Debug plugin: stop called")
