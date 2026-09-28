"""Stateless JSON MCP over API Gateway; no local catalogue or write credentials."""
import os
from mangum import Mangum
from mcp.server.transport_security import TransportSecuritySettings
from server import Library, PUBLIC_SITE, create_server


def handler(event, context):
    # MCP's session manager is single-use. Recreate it on warm invocations too.
    server = create_server(Library(os.environ['CATALOG_API_URL'], os.environ.get('SITE_URL', PUBLIC_SITE)), host='0.0.0.0')
    domain = event['requestContext']['domainName']
    server.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=[domain],
        allowed_origins=['https://chatgpt.com'],
    )
    return Mangum(server.streamable_http_app(), lifespan='on')(event, context)
