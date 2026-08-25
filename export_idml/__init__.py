def classFactory(iface):
    from .plugin import ExportIdmlPlugin

    return ExportIdmlPlugin(iface)
