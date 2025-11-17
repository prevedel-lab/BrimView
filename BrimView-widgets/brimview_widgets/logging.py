# Wether to use the panel logger, whose output is shown in the admin panel 
_use_panel_logger = False

from .environment import running_from_pyodide

if running_from_pyodide:
    import js
    from enum import Enum
    class PyodideLogger:
        class LogLevel(Enum):
            DEBUG = 1,
            INFO = 2,
            WARNING = 3,
            ERROR = 4,
            CRITICAL = 5 
        log_level = LogLevel.INFO
        def _log(self, msg, level: LogLevel): 
                if level.value>=self.log_level.value:
                    js.console.log(f"{level.name}: {msg}")
        def debug (self, msg):
            self._log(msg, PyodideLogger.LogLevel.DEBUG) 
        def info (self, msg):
            self._log(msg, PyodideLogger.LogLevel.INFO)
        def warning (self, msg):
            self._log(msg, PyodideLogger.LogLevel.WARNING) 
        def error (self, msg):
            self._log(msg, PyodideLogger.LogLevel.ERROR)
        def critical (self, msg):
            self._log(msg, PyodideLogger.LogLevel.CRITICAL) 
    logger = PyodideLogger()
else:
    if _use_panel_logger:
        raise NotImplementedError('Logging with "pn.state.log" raises an error when the admin panel is open')
        import panel as pn
        class PanelLogger:
            def _log(self, msg, level): 
                pn.state.log(msg, level)
            def debug (self, msg):
                self._log(msg, 'debug') 
            def info (self, msg):
                self._log(msg, 'info')
            def warning (self, msg):
                self._log(msg, 'warning') 
            def error (self, msg):
                self._log(msg, 'error')
            def critical (self, msg):
                self._log(msg, 'critical') 
        logger = PanelLogger()
    else:
        import logging
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.DEBUG)
