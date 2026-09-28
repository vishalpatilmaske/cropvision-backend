"""Sign in with Google: verify the access token from Google's sign-in popup.

The browser gets a short-lived access token from Google (scopes: openid,
email, profile). Before trusting it we ask Google's tokeninfo endpoint about
it and require that it was issued to *our* OAuth Client ID -- otherwise a
token another app obtained for the same person could be replayed here. Then
the userinfo endpoint gives the verified email and display name.
"""
from typing import Any, Dict

import requests
from flask import current_app

TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


class GoogleAuthError(Exception):
    """The token is invalid or not for this app (maps to 401)."""


class GoogleUnavailableError(Exception):
    """Google couldn't be reached (maps to 502)."""


def _get_json(url: str, **kwargs) -> requests.Response:
    try:
        return requests.get(url, timeout=10, **kwargs)
    except requests.RequestException as exc:
        raise GoogleUnavailableError("Couldn't reach Google. Please try again.") from exc


def verify_google_access_token(access_token: str) -> Dict[str, Any]:
    """Returns {"email", "name", "google_id"} for a valid token issued to our app."""
    client_id = current_app.config.get("GOOGLE_CLIENT_ID")
    if not client_id:
        raise GoogleAuthError("Google sign-in is not configured.")

    info_resp = _get_json(TOKENINFO_URL, params={"access_token": access_token})
    if info_resp.status_code != 200:
        raise GoogleAuthError("Invalid or expired Google sign-in. Please try again.")
    info = info_resp.json()
    if client_id not in (info.get("aud"), info.get("azp")):
        raise GoogleAuthError("This Google sign-in was not issued for CropVision AI.")

    user_resp = _get_json(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
    if user_resp.status_code != 200:
        raise GoogleAuthError("Couldn't read your Google profile. Please try again.")
    profile = user_resp.json()

    email = (profile.get("email") or "").strip().lower()
    if not email or profile.get("email_verified") is not True:
        raise GoogleAuthError("Your Google account email is not verified.")
    if profile.get("sub") != info.get("sub"):
        raise GoogleAuthError("Invalid Google sign-in. Please try again.")

    return {
        "email": email,
        "name": (profile.get("name") or email.split("@")[0]).strip()[:120],
        "google_id": profile.get("sub"),
    }
