from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


def connect(url):
    engine = create_async_engine(url, pool_pre_ping=True, echo=False, hide_parameters=True)
    return engine, async_sessionmaker(engine, expire_on_commit=False)
