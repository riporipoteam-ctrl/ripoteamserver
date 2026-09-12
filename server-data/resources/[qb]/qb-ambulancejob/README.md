# qb-ambulancejob
EMS Job and Death/Wound Logic for QB-Core Framework :ambulance:

## Dependencies
- [qb-core](https://github.com/qbcore-framework/qb-core) (Required)
- [qb-phone](https://github.com/qbcore-framework/qb-phone) (Required)
- [qb-target](https://github.com/BerkieBb/qb-target) (Optional)
- [PolyZone](https://github.com/mkafrin/PolyZone) (Required)

# Server.cfg Convar Update
- Global DrawTextUi Option
```
setr UseTarget false
``` 

- Global Target Option
```
setr UseTarget true
```


# Medal Clip Capture

Asks [Medal](https://medal.tv) to save a clip when a player goes down, so players can catch their funny
deaths. **This is enabled by default.** There is nothing to configure to use it.

It only does anything for players who already have Medal installed and running. The clip is cut from the
replay buffer Medal is already keeping and saved to that player's own Medal library, tagged `qbcore` so they
can search for it. Neither your server nor QBCore receives the clip, and this resource uploads nothing
itself, but Medal may sync the clip to its own servers depending on the player's Medal account and settings,
the same as any other clip Medal captures.

To turn it off for your server, set this in `config.lua`:

```lua
Config.Medal = { Enabled = false }
```

Medal listens on the player's own machine, so the request is sent from the client through a hidden NUI frame
(`html/medal.js`) rather than from the server. It is fire and forget, nothing is reported back, and players
without Medal running just get a request that goes nowhere. Everything else is hardcoded in
`client/medal.lua`.

# License

    QBCore Framework
    Copyright (C) 2021 Joshua Eger

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>
