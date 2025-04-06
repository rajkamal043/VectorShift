# # slack.py

# from fastapi import Request

# async def authorize_hubspot(user_id, org_id):
#     # TODO
#     pass

# async def oauth2callback_hubspot(request: Request):
#     # TODO
#     pass

# async def get_hubspot_credentials(user_id, org_id):
#     # TODO
#     pass

# async def create_integration_item_metadata_object(response_json):
#     # TODO
#     pass

# async def get_items_hubspot(credentials):
#     # TODO
#     pass


import sys, os
import json
import secrets
import base64
import asyncio
from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse
import httpx
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from redis_client_m import add_key_value_redis, get_value_redis, delete_key_redis
from integrations.integration_item import IntegrationItem

# Replace with your actual credentials
CLIENT_ID = "6ee57fee-7083-4ff3-a54c-e225b3799a4f"
CLIENT_SECRET = "16516e72-761e-4c69-89ca-a003275a2d97"
REDIRECT_URI = "http://localhost:3000/integrations/hubspot/oauth2callback"
SCOPE = "contacts"

AUTH_URL = "https://app.hubspot.com/oauth/authorize"
TOKEN_URL = "https://api.hubapi.com/oauth/v1/token"
API_BASE_URL = "https://api.hubapi.com"

# 1. AUTHORIZATION
async def authorize_hubspot(user_id, org_id):
    state_data = {
        "state": secrets.token_urlsafe(32),
        "user_id": user_id,
        "org_id": org_id,
    }
    encoded_state = base64.urlsafe_b64encode(json.dumps(state_data).encode("utf-8")).decode("utf-8")

    auth_url = (
        f"{AUTH_URL}"
        f"?client_id={CLIENT_ID}"
        f"&scope={SCOPE}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&state={encoded_state}"
        f"&response_type=code"
    )

    await add_key_value_redis(f"hubspot_state:{org_id}:{user_id}", json.dumps(state_data), expire=600)
    return auth_url

# 2. OAUTH CALLBACK
async def oauth2callback_hubspot(request: Request):
    if request.query_params.get("error"):
        raise HTTPException(status_code=400, detail=request.query_params.get("error_description"))

    code = request.query_params.get("code")
    encoded_state = request.query_params.get("state")
    state_data = json.loads(base64.urlsafe_b64decode(encoded_state.encode()).decode("utf-8"))

    user_id = state_data["user_id"]
    org_id = state_data["org_id"]
    original_state = state_data["state"]

    saved_state = await get_value_redis(f"hubspot_state:{org_id}:{user_id}")
    if not saved_state or original_state != json.loads(saved_state)["state"]:
        raise HTTPException(status_code=400, detail="State mismatch")

    await delete_key_redis(f"hubspot_state:{org_id}:{user_id}")

    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "redirect_uri": REDIRECT_URI,
                "code": code,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    credentials = token_response.json()
    await add_key_value_redis(f"hubspot_credentials:{org_id}:{user_id}", json.dumps(credentials), expire=600)

    return HTMLResponse(
        content="""
        <html><script>window.close();</script></html>
        """
    )

# 3. GET STORED CREDENTIALS
async def get_hubspot_credentials(user_id, org_id):
    credentials = await get_value_redis(f"hubspot_credentials:{org_id}:{user_id}")
    if not credentials:
        raise HTTPException(status_code=400, detail="No credentials found.")
    credentials = json.loads(credentials)
    await delete_key_redis(f"hubspot_credentials:{org_id}:{user_id}")
    return credentials

def create_integration_item_metadata_object(
    contact, item_type="Contact", parent_id=None, parent_name=None
) -> IntegrationItem:
    return IntegrationItem(
        id=contact.get("id"),
        name=contact.get("properties", {}).get("firstname", "Unknown"),
        type=item_type,
        parent_id=parent_id,
        parent_path_or_name=parent_name,
    )

async def get_items_hubspot(credentials) -> list[IntegrationItem]:
    access_token = credentials["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{API_BASE_URL}/crm/v3/objects/contacts",
            headers=headers,
            params={"limit": 10}  # optional: adjust how many contacts you fetch
        )

    if response.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to fetch contacts")

    contacts = response.json().get("results", [])
    integration_items = [
        create_integration_item_metadata_object(contact) for contact in contacts
    ]

    print(f"[HubSpot] Retrieved {len(integration_items)} contacts")
    return integration_items
