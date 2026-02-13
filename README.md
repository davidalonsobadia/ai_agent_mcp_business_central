# Business Central MCP Integration (knowall-ai)

## 🎯 ¿Qué es esto?

Esta es una integración completa con el servidor MCP de **knowall-ai** para Business Central. A diferencia del MCP oficial de Microsoft (que solo funciona con Copilot Studio), este servidor MCP:

✅ **Expone APIs REST estándar** que puedes llamar desde Python
✅ **Usa el protocolo MCP estándar** (JSON-RPC sobre stdio)
✅ **Tiene autodiscovery** - el agente descubre qué puede hacer
✅ **Funciona con cualquier cliente MCP** - no solo Copilot Studio

## 📦 Archivos del proyecto

1. **bc_mcp_client_knowall.py** – Cliente Python para el servidor MCP
2. **fastapi_agent_knowall.py** – API y agente de IA con FastAPI
3. **.env.example** – Plantilla de variables de entorno (copiar a `.env`)
4. **requirements.txt** – Dependencias Python
5. **README_KNOWALL.md** – Esta guía

## 🔑 Diferencia Clave

### MCP Oficial de Microsoft
```
Business Central MCP → SOLO Copilot Studio
                     → No se puede llamar directamente desde Python
                     → Endpoints REST no expuestos
```

### MCP de knowall-ai
```
Business Central MCP Server (knowall-ai)
                     → Cliente Python ✅
                     → FastAPI Agent ✅
                     → Claude Desktop ✅
                     → Cualquier cliente MCP ✅
```

## 🚀 Inicio Rápido

### Prerrequisitos

```bash
# 1. Node.js (para npx / servidor MCP)
node --version  # v18+

# 2. Python 3.10+
python3 --version

# 3. Variables de entorno: copia .env.example a .env y rellena valores
cp .env.example .env
# Edita .env con BC_URL_SERVER, BC_COMPANY, BC_AUTH_TYPE, y si usas client_credentials: BC_TENANT_ID, BC_CLIENT_ID, BC_CLIENT_SECRET
# Para el agente: OPENAI_API_KEY
```

**Autenticación:** El paquete npm publicado solo soporta `azure_cli`. Para `client_credentials` hay que clonar el [repositorio MCP](https://github.com/knowall-ai/mcp-business-central), hacer `npm run build` y usar el build local (el cliente lo detecta en `./mcp-business-central-local/build/index.js`).

### Instalación

```bash
pip install -r requirements.txt
```

### Usar el cliente Python

```python
from bc_mcp_client_knowall import BusinessCentralMCPClient, load_bc_config_from_env

# Cargar configuración desde .env / entorno
config = load_bc_config_from_env()
client = BusinessCentralMCPClient(config)
await client.start()

# Listar clientes
customers = await client.list_items("customers", top=10)

# Obtener esquema
schema = await client.get_schema("items")

await client.stop()
```

### Ejecutar el agente de IA (FastAPI)

```bash
# Asegúrate de tener .env con OPENAI_API_KEY y variables BC_*
python fastapi_agent_knowall.py
```

Prueba el chat:
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "¿Cuántos clientes tenemos?"
  }'
```

## 🛠️ Cómo Funciona

### Arquitectura del Sistema

```
Usuario → FastAPI → OpenAI (GPT-4) → MCP Client → knowall-ai MCP Server → Business Central
                         ↓                               ↓
                  Function Calling             JSON-RPC over stdio
```

### Flujo de una Consulta

```
1. Usuario: "¿Cuántos clientes activos tenemos?"

2. FastAPI recibe el mensaje

3. OpenAI con function calling:
   - Recibe el mensaje
   - Ve las herramientas MCP disponibles
   - Decide usar: list_items("customers")

4. FastAPI ejecuta la herramienta vía MCP Client:
   client.call_tool("list_items", {"resource": "customers"})

5. MCP Client → knowall-ai MCP Server (JSON-RPC)

6. knowall-ai MCP Server:
   - Obtiene token de Azure CLI
   - Llama a Business Central API
   - Devuelve datos

7. OpenAI recibe los datos y genera respuesta:
   "Tienes 150 clientes activos en Business Central"
```

## 🔍 Herramientas Disponibles

El servidor MCP de knowall-ai expone 6 herramientas:

### 1. get_schema
Obtiene el esquema OData de un recurso

```python
schema = await client.get_schema("customers")
# Retorna: metadata XML del esquema
```

### 2. list_items
Lista items con filtros y paginación

```python
items = await client.list_items(
    resource="customers",
    filter="displayName eq 'Contoso'",
    top=10,
    skip=0
)
```

### 3. get_items_by_field
Busca items por un campo específico

```python
results = await client.get_items_by_field(
    resource="contacts",
    field="companyName",
    value="Contoso Ltd"
)
```

### 4. create_item
Crea un nuevo item

```python
new_customer = await client.create_item(
    resource="customers",
    item_data={
        "displayName": "Nuevo Cliente",
        "email": "[email protected]"
    }
)
```

### 5. update_item
Actualiza un item existente

```python
updated = await client.update_item(
    resource="customers",
    item_id="guid-del-cliente",
    item_data={
        "displayName": "Nombre Actualizado"
    }
)
```

### 6. delete_item
Elimina un item

```python
result = await client.delete_item(
    resource="customers",
    item_id="guid-del-cliente"
)
```

## 📊 Recursos Comunes de Business Central

Estos son los recursos que típicamente estarán disponibles:

- `companies` - Información de compañías
- `customers` - Clientes
- `contacts` - Contactos
- `items` - Productos/Items
- `vendors` - Proveedores
- `salesOpportunities` - Oportunidades de venta
- `salesQuotes` - Cotizaciones
- `salesOrders` - Órdenes de venta
- `salesInvoices` - Facturas de venta
- `purchaseOrders` - Órdenes de compra

## 🔐 Autenticación

### Opción 1: Azure CLI (Recomendado para desarrollo)

```bash
# 1. Instalar Azure CLI
# https://docs.microsoft.com/cli/azure/install-azure-cli

# 2. Autenticarse
az login

# 3. Verificar que funciona
az account get-access-token --resource https://api.businesscentral.dynamics.com

# 4. Configurar en tu código
config = BCMCPConfig(
    ...
    bc_auth_type="azure_cli"
)
```

**Ventajas:**
- ✅ Fácil de configurar
- ✅ Usa tus credenciales personales
- ✅ Ideal para desarrollo

**Desventajas:**
- ❌ No funciona en servidores sin interfaz
- ❌ Requiere login manual periódico

### Opción 2: Client Credentials (Para producción)

```python
config = BCMCPConfig(
    bc_url_server="...",
    bc_company="...",
    bc_auth_type="client_credentials",
    client_id="tu-client-id",
    client_secret="tu-client-secret",
    tenant_id="tu-tenant-id"
)
```

**Configuración en Azure:**
1. Azure Portal → App Registrations → Nueva app
2. Certificates & secrets → Nuevo secret
3. API permissions → Agregar Dynamics 365 Business Central
4. Grant admin consent

**Ventajas:**
- ✅ Funciona en servidores
- ✅ No requiere intervención humana
- ✅ Ideal para producción

## 🔗 Endpoints de FastAPI

### POST /chat
Chat con el agente

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Lista los últimos 5 clientes",
    "conversation_id": "conv_123"
  }'
```

### GET /mcp/status
Estado del servidor MCP

```bash
curl http://localhost:8000/mcp/status
```

### GET /mcp/tools
Lista de herramientas disponibles

```bash
curl http://localhost:8000/mcp/tools
```

### GET /mcp/resources
Recursos de BC accesibles

```bash
curl http://localhost:8000/mcp/resources
```

### POST /mcp/call
Llamar directamente a una herramienta

```bash
curl -X POST "http://localhost:8000/mcp/call?tool_name=list_items" \
  -H "Content-Type: application/json" \
  -d '{
    "resource": "customers",
    "top": 5
  }'
```

## 🐛 Troubleshooting

### Error: "npx no instalado"
```bash
# Instalar Node.js desde:
https://nodejs.org/
```

### Error: "Azure CLI no autenticado"
```bash
az login
az account show  # Verificar login
```

### Error: "No se puede conectar a Business Central"
```bash
# Verificar URL (debe terminar en /api/v2.0)
# Verificar nombre de compañía (case-sensitive)
# Verificar permisos en Azure AD
```

### Error: "Servidor MCP no responde"
```bash
# Probar manualmente el servidor
npx @knowall-ai/mcp-business-central

# Ver logs
# El servidor imprime errores en stderr
```

### Error: "Resource not found"
```bash
# Algunos recursos pueden no estar disponibles
# Usa el script verify_mcp_knowall.py para ver qué recursos funcionan
```

## 📚 Recursos y Links

- [knowall-ai MCP Server](https://github.com/knowall-ai/mcp-business-central)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [Business Central API v2.0](https://learn.microsoft.com/en-us/dynamics365/business-central/dev-itpro/api-reference/v2.0/)
- [Azure CLI](https://docs.microsoft.com/cli/azure/install-azure-cli)

## ✨ Ejemplos de Uso

### Ejemplo 1: Análisis de clientes
```python
# Obtener todos los clientes
customers = await client.list_items("customers", top=100)

# Analizar con AI
response = await agent.process_message(
    "Analiza estos clientes y dame insights",
    []
)
```

### Ejemplo 2: Crear cotización
```python
# Buscar cliente
customer = await client.get_items_by_field(
    "customers",
    "displayName",
    "Contoso Ltd"
)

# Crear cotización
quote = await client.create_item("salesQuotes", {
    "customerId": customer[0]["id"],
    "quoteDate": "2026-02-13",
    # ... más campos
})
```

### Ejemplo 3: Dashboard en tiempo real
```python
# FastAPI endpoint para dashboard
@app.get("/dashboard")
async def dashboard():
    customers_count = len(await client.list_items("customers"))
    orders_today = await client.list_items(
        "salesOrders",
        filter="orderDate eq 2026-02-13"
    )
    
    return {
        "customers": customers_count,
        "orders_today": len(orders_today)
    }
```

## 🎯 Próximos Pasos

1. ✅ Ejecuta `verify_mcp_knowall.py`
2. ✅ Prueba el cliente Python
3. ✅ Configura tu API key de OpenAI
4. ✅ Ejecuta el agente de FastAPI
5. ✅ Prueba queries en lenguaje natural
6. 🚀 Customiza para tu caso de uso

## 💡 Tips

- **Cachea los schemas**: No llames a `get_schema` en cada request
- **Usa filtros OData**: Son más eficientes que obtener todo y filtrar en Python
- **Maneja errores**: Las APIs pueden fallar, siempre usa try/catch
- **Limita resultados**: Usa `top` para evitar obtener miles de registros
- **Monitoriza**: Log todas las llamadas al MCP para debugging

---

¿Preguntas? Ejecuta el script de verificación y revisa los logs para más detalles.
