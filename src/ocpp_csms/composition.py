from __future__ import annotations

from ocpp_csms.handlers.authorize import AuthorizeHandler
from ocpp_csms.handlers.boot import BootNotificationHandler
from ocpp_csms.handlers.heartbeat import HeartbeatHandler
from ocpp_csms.handlers.meter_values import MeterValuesHandler
from ocpp_csms.handlers.status import StatusNotificationHandler
from ocpp_csms.handlers.transactions import StartTransactionHandler, StopTransactionHandler
from ocpp_csms.routing import HandlerRegistry
from ocpp_csms.server import CSMSServer
from ocpp_csms.services.auth import AuthorizationService
from ocpp_csms.services.chargers import ChargerService
from ocpp_csms.services.transactions import TransactionService


def build_registry(
    chargers: ChargerService,
    authorization: AuthorizationService,
    transactions: TransactionService,
) -> HandlerRegistry:
    registry = HandlerRegistry()
    registry.register("BootNotification", BootNotificationHandler(chargers))
    registry.register("Heartbeat", HeartbeatHandler())
    registry.register("Authorize", AuthorizeHandler(authorization))
    registry.register("StatusNotification", StatusNotificationHandler(chargers))
    registry.register("StartTransaction", StartTransactionHandler(authorization, transactions))
    registry.register("StopTransaction", StopTransactionHandler(transactions))
    registry.register("MeterValues", MeterValuesHandler(transactions))
    return registry


def build_server(host: str, port: int) -> CSMSServer:
    chargers = ChargerService()
    authorization = AuthorizationService()
    transactions = TransactionService()
    registry = build_registry(chargers, authorization, transactions)
    return CSMSServer(host=host, port=port, registry=registry)
