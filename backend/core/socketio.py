"""Socket.IO setup with tenant-scoped rooms.

Per MASTER_BUILD_PLAN Phase 2B:
- Socket.IO: tenant-scoped rooms for real-time updates
- Per CLAUDE.md: Socket.IO + Redis pub/sub for live updates
"""

import socketio
import structlog

from backend.core.config import settings

logger = structlog.get_logger()

# Create Socket.IO server with Redis manager for scaling
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=settings.CORS_ORIGINS,
    logger=False,
    engineio_logger=False,
)

# ASGI app for mounting in FastAPI
sio_app = socketio.ASGIApp(sio, socketio_path="/ws/socket.io")


@sio.event
async def connect(sid: str, environ: dict) -> None:
    """Handle new Socket.IO connection."""
    logger.info("socketio_connect", sid=sid)


@sio.event
async def disconnect(sid: str) -> None:
    """Handle Socket.IO disconnection."""
    logger.info("socketio_disconnect", sid=sid)


@sio.event
async def join_tenant(sid: str, data: dict) -> None:
    """Join a tenant-scoped room for real-time updates.

    Per MASTER_BUILD_PLAN: tenant-scoped rooms.
    Client sends: {"tenant_id": "xxx"}
    """
    tenant_id = data.get("tenant_id")
    if not tenant_id:
        return
    room = f"tenant:{tenant_id}"
    sio.enter_room(sid, room)
    logger.info("socketio_join_tenant", sid=sid, tenant_id=tenant_id)


@sio.event
async def leave_tenant(sid: str, data: dict) -> None:
    """Leave a tenant-scoped room."""
    tenant_id = data.get("tenant_id")
    if not tenant_id:
        return
    room = f"tenant:{tenant_id}"
    sio.leave_room(sid, room)
    logger.info("socketio_leave_tenant", sid=sid, tenant_id=tenant_id)


async def emit_to_tenant(tenant_id: str, event: str, data: dict) -> None:
    """Emit an event to all connections in a tenant room."""
    room = f"tenant:{tenant_id}"
    await sio.emit(event, data, room=room)
    logger.debug("socketio_emit", event=event, tenant_id=tenant_id)


async def emit_to_user(tenant_id: str, user_id: str, event: str, data: dict) -> None:
    """Emit an event to a specific user within a tenant.

    Per Phase 11: real-time agent responses scoped to the requesting user.
    """
    room = f"user:{tenant_id}:{user_id}"
    await sio.emit(event, data, room=room)
    logger.debug("socketio_emit_user", event=event, tenant_id=tenant_id, user_id=user_id)


@sio.event
async def join_user_channel(sid: str, data: dict) -> None:
    """Join a user-specific channel for agent chat updates.

    Per Phase 11: agent responses streamed to the requesting user.
    Client sends: {"tenant_id": "xxx", "user_id": "yyy"}
    """
    tenant_id = data.get("tenant_id")
    user_id = data.get("user_id")
    if not tenant_id or not user_id:
        return
    room = f"user:{tenant_id}:{user_id}"
    sio.enter_room(sid, room)
    logger.info("socketio_join_user_channel", sid=sid, tenant_id=tenant_id, user_id=user_id)


@sio.event
async def leave_user_channel(sid: str, data: dict) -> None:
    """Leave a user-specific channel."""
    tenant_id = data.get("tenant_id")
    user_id = data.get("user_id")
    if not tenant_id or not user_id:
        return
    room = f"user:{tenant_id}:{user_id}"
    sio.leave_room(sid, room)


@sio.event
async def agent_typing(sid: str, data: dict) -> None:
    """Broadcast agent typing indicator to the user's channel.

    Client sends: {"tenant_id": "xxx", "user_id": "yyy", "agent_id": "supply_chain"}
    """
    tenant_id = data.get("tenant_id")
    user_id = data.get("user_id")
    if not tenant_id or not user_id:
        return
    room = f"user:{tenant_id}:{user_id}"
    await sio.emit("agent_typing", {
        "agent_id": data.get("agent_id"),
        "agent_name": data.get("agent_name", "ESG Agent"),
    }, room=room)
