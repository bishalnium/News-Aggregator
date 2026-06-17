import os
import asyncio
import firebase_admin
from firebase_admin import credentials, messaging
from config import settings

_fcm_initialized = False

def init_fcm():
    global _fcm_initialized
    if _fcm_initialized:
        return True
    
    cred_path = settings.fcm_credentials_path
    if not cred_path:
        # Check if default path exists
        default_path = "data/firebase_service_account.json"
        if os.path.exists(default_path):
            cred_path = default_path
            
    if not cred_path or not os.path.exists(cred_path):
        # Silent warning so it doesn't fail if user hasn't set up Firebase yet
        return False
        
    try:
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
        _fcm_initialized = True
        print("FCM NOTIFICATION: Firebase Admin SDK initialized successfully.")
        return True
    except Exception as e:
        print(f"FCM NOTIFICATION: Failed to initialize Firebase Admin SDK: {e}")
        return False


async def send_push_notification(tokens: list[str], title: str, body: str, alert_type: str) -> None:
    if not tokens:
        return
    if not init_fcm():
        return

    channel_id = "keyword_alerts" if alert_type == "keyword" else "context_alerts"
    sound_name = "keyword_alert" if alert_type == "keyword" else "context_alert"
    
    # We send both visual notification and custom android sound channel
    message = messaging.MulticastMessage(
        notification=messaging.Notification(
            title=title,
            body=body,
        ),
        data={
            "alert_type": alert_type,
        },
        android=messaging.AndroidConfig(
            notification=messaging.AndroidNotification(
                channel_id=channel_id,
                sound=sound_name,
            )
        ),
        tokens=tokens,
    )
    
    try:
        response = await asyncio.to_thread(messaging.send_multicast, message)
        print(f"FCM: Sent notification to {len(tokens)} devices. Success: {response.success_count}, Failure: {response.failure_count}")
    except Exception as e:
        print(f"FCM: Send error: {e}")
