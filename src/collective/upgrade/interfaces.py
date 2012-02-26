from zope import interface


class IUpgrader(interface.Interface):
    """Upgrade a context."""

    logger = interface.Attribute(
        'The logger used to record upgrade information.')
    log_level = interface.Attribute(
        'The default "logging" module level for log messages.')
    log_template = interface.Attribute(
        'The string formatting template used for log messagess.')

    def __call__():
        """Do the actual upgrade work."""

    def log(msg, level=None, template=None):
        """Log a message using the template and level."""

    def commit():
        """Do a 'transaction.commit()' of upgrade progess with a log message"""


class IPortalUpgrader(IUpgrader):
    """Upgrade a individual portal."""


class IMultiPortalUpgrader(IUpgrader):
    """Upgrade multiple portals."""
