# Events, a FiestaBoard plugin

Count down to events, with a message on the day. No calendar needed, all details are stored in plugin settings.

## Configure

| Setting | Notes |
|---|---|
| Events | One row per event: name, date, optional end date (`YYYY-MM-DD`), optional message and colors. |
| Timezone | Defaults to America/Chicago. |
| Upcoming Window | How far ahead `events.upcoming` looks. Default 60 days. |
| Take Over the Board | Takes over on event days. Off by default. |
| Takeover Priority | Higher number wins. Default 35. |

### Page recipes

Today's event, or the next one:

```text
{{= IFERROR(IF(events.is_event_day = "true", events.message, events.next_formatted), "") }}
```

Supports Vestaboard and Vestaboard Note
