from dagger.client._session import default_session

_shared = default_session()
connect = _shared.connect
close = _shared.close
