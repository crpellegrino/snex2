from .apps import DashSpectraConfig
import logging
logger = logging.getLogger(__name__)


def loader(app_name):
    if 'dash_spectra' in app_name:
        logger.info(f"{app_name} is being loaded")
        return DashSpectraConfig
    else:
        return None
