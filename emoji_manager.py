from maubot import Plugin, MessageEvent
from maubot.handlers import command
from mautrix.types import EventType


EMOTES_TYPE = EventType("im.ponies.room_emotes")


class EmojiManager(Plugin):

    @command.new("addemoji", help="Add custom emoji - Usage: !addemoji <name> <mxc_url>")
    @command.argument("name", required=True)
    @command.argument("mxc_url", required=True)
    async def add_emoji(self, evt: MessageEvent, name: str, mxc_url: str) -> None:
        if not mxc_url.startswith("mxc://"):
            await evt.reply("Invalid MXC URL. Must start with mxc://")
            return

        try:
            try:
                current = await self.client.get_state_event(
                    evt.room_id, EMOTES_TYPE, ""
                )
                emoticons = current.get("emoticons", {})
            except Exception:
                emoticons = {}

            emoticons[name] = {"url": mxc_url}

            await self.client.send_state_event(
                evt.room_id,
                EMOTES_TYPE,
                {"emoticons": emoticons},
                state_key="",
            )

            await evt.reply(f"Added emoji :{name}:")
        except Exception as e:
            await evt.reply(f"Error adding emoji: {e}")

    @command.new("listemojis", help="List all custom emojis in this room")
    async def list_emojis(self, evt: MessageEvent) -> None:
        try:
            current = await self.client.get_state_event(
                evt.room_id, EMOTES_TYPE, ""
            )
            emoticons = current.get("emoticons", {})

            if not emoticons:
                await evt.reply("No custom emojis in this room")
                return

            emoji_list = "\n".join(
                [f":{name}: - {data['url']}" for name, data in emoticons.items()]
            )
            await evt.reply(f"Custom emojis:\n{emoji_list}")
        except Exception:
            await evt.reply("No custom emojis in this room")

    @command.new(
        "removeemoji", help="Remove custom emoji - Usage: !removeemoji <name>"
    )
    @command.argument("name", required=True)
    async def remove_emoji(self, evt: MessageEvent, name: str) -> None:
        try:
            current = await self.client.get_state_event(
                evt.room_id, EMOTES_TYPE, ""
            )
            emoticons = current.get("emoticons", {})

            if name not in emoticons:
                await evt.reply(f"Emoji :{name}: not found")
                return

            del emoticons[name]

            await self.client.send_state_event(
                evt.room_id,
                EMOTES_TYPE,
                {"emoticons": emoticons},
                state_key="",
            )

            await evt.reply(f"Removed emoji :{name}:")
        except Exception as e:
            await evt.reply(f"Error: {e}")
