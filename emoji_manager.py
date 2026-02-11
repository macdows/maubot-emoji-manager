import re

from maubot import Plugin, MessageEvent
from maubot.handlers import command
from mautrix.types import EventType


EMOTES_TYPE = EventType("im.ponies.room_emotes", EventType.Class.STATE)
SHORTCODE_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
SHORTCODE_MAX_BYTES = 100


def serialize_content(content) -> dict:
    if hasattr(content, "serialize"):
        return content.serialize()
    if isinstance(content, dict):
        return content
    return {}


def get_images(content: dict) -> dict:
    return content.get("images") or content.get("emoticons") or {}


def get_pack_meta(content: dict) -> dict:
    return content.get("pack") or {}


def validate_shortcode(shortcode: str) -> tuple[bool, str]:
    if not SHORTCODE_RE.match(shortcode):
        return False, "Shortcode must only contain letters, numbers, hyphens, and underscores."
    if len(shortcode.encode("utf-8")) > SHORTCODE_MAX_BYTES:
        return False, f"Shortcode must be {SHORTCODE_MAX_BYTES} bytes or less."
    return True, ""


def build_pack_content(images: dict, pack_meta: dict) -> dict:
    content = {"images": images}
    if pack_meta:
        content["pack"] = pack_meta
    return content


class EmojiManager(Plugin):

    async def _read_pack(self, room_id) -> tuple[dict, dict]:
        try:
            raw = await self.client.get_state_event(room_id, EMOTES_TYPE, "")
            content = serialize_content(raw)
        except Exception:
            content = {}
        return get_images(content), get_pack_meta(content)

    @command.new("emoji", help="Manage custom emojis. Subcommands: add, remove, list")
    async def emoji(self, evt: MessageEvent) -> None:
        pass

    @emoji.subcommand("add", help="Add emoji — !emoji add <shortcode> <mxc_url>")
    @command.argument("shortcode", required=True)
    @command.argument("mxc_url", required=True)
    async def add_emoji(self, evt: MessageEvent, shortcode: str, mxc_url: str) -> None:
        valid, reason = validate_shortcode(shortcode)
        if not valid:
            await evt.reply(reason)
            return

        if not mxc_url.startswith("mxc://"):
            await evt.reply("Invalid MXC URL. Must start with mxc://")
            return

        try:
            images, pack_meta = await self._read_pack(evt.room_id)
            images[shortcode] = {"url": mxc_url}

            await self.client.send_state_event(
                evt.room_id,
                EMOTES_TYPE,
                build_pack_content(images, pack_meta),
                state_key="",
            )
            await evt.reply(f"Added emoji :{shortcode}:")
        except Exception as e:
            await evt.reply(f"Error adding emoji: {e}")

    @emoji.subcommand("remove", help="Remove emoji — !emoji remove <shortcode>")
    @command.argument("shortcode", required=True)
    async def remove_emoji(self, evt: MessageEvent, shortcode: str) -> None:
        try:
            images, pack_meta = await self._read_pack(evt.room_id)

            if shortcode not in images:
                await evt.reply(f"Emoji :{shortcode}: not found")
                return

            del images[shortcode]

            await self.client.send_state_event(
                evt.room_id,
                EMOTES_TYPE,
                build_pack_content(images, pack_meta),
                state_key="",
            )
            await evt.reply(f"Removed emoji :{shortcode}:")
        except Exception as e:
            await evt.reply(f"Error: {e}")

    @emoji.subcommand("list", help="List all custom emojis in this room")
    async def list_emojis(self, evt: MessageEvent) -> None:
        try:
            images, _ = await self._read_pack(evt.room_id)

            if not images:
                await evt.reply("No custom emojis in this room")
                return

            emoji_list = "\n".join(
                f":{name}: — {data['url']}" for name, data in images.items()
            )
            await evt.reply(f"Custom emojis:\n{emoji_list}")
        except Exception as e:
            self.log.error(f"Failed to list emojis: {e}")
            await evt.reply("No custom emojis in this room")
