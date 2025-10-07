# Wether to use the panel logger, whose output is shown in the admin panel 
_use_panel_logger = False

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