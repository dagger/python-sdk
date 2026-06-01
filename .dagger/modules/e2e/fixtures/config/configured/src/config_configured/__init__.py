import dagger
from dagger import object_type


@object_type
class ConfigConfigured:
    source: dagger.Directory

    def __init__(self, ws: dagger.Workspace):
        self.source = ws.directory("/")
