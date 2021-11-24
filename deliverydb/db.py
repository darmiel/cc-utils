import logging

import psycopg
import sqlalchemy

from deliverydb.model import Base, Scan


class DeliveryDB:
    def __init__(
        self,
        username: str,
        password: str,
        hostname: str,
        port: int,
        dialect: str = 'postgresql',
    ):
        self._engine = sqlalchemy.create_engine(
            f'{dialect}://{username}:{password}@{hostname}:{port}',
            echo=True,
            future=True,
        )

        self.Base = Base
        # we configured our own root logger and use log propagation
        # therefore pop streamhandler to not have duplicate output
        logging.getLogger('sqlalchemy.engine.Engine').handlers.pop()
        self.Base.metadata.create_all(self._engine)
        self.Session = sqlalchemy.orm.Session(self._engine)

    def insert_compliance_issue(
        self,
        artifact: dict,
        meta: dict,
        data: dict,
    ):
        scan = Scan(
            artifact=artifact,
            meta=meta,
            data=data,
        )

        self.Session.add(scan)


def delivery_db_no_orm(
    username: str,
    password: str,
    hostname: str,
    port: int,
) -> psycopg.Connection:

    return psycopg.connect(
        user=username,
        password=password,
        host=hostname,
        port=port,
    )


def database_conncetion_url_from_cfg(
    db_cfg: model.compliancedb.ComplianceDbConfig,
    dialect: str = 'postgres',
) -> str:
    username = db_cfg.credentials().username()
    password = db_cfg.credentials().password()
    hostname = db_cfg.hostname()
    port = db_cfg.port()

    # Potentially, this function could be used to create connection urls
    # to different types of databases, therefore the "dialect" can vary.
    return f'{dialect}://{username}:{password}@{hostname}:{port}',


def database_connection_url_from_custom(
    hostname: str,
    username: str,
    password: str,
    port: int,
    dialect: str = 'postgres',
) -> str:
    return f'{dialect}://{username}:{password}@{hostname}:{port}'
