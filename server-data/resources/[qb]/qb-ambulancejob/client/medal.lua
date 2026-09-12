local ENDPOINT = 'http://localhost:12665/api/v1/event/invoke'
local PUBLIC_KEY = 'pub_82qkpMKV77AkpqLSgWsxLlDyfzpPI7Vw'
local COOLDOWN = 20000

local lastClipAt = 0

function TriggerMedalClip()
    if not Config.Medal or not Config.Medal.Enabled then return end

    local now = GetGameTimer()
    if lastClipAt ~= 0 and now - lastClipAt < COOLDOWN then return end
    lastClipAt = now

    SendNUIMessage({
        action = 'medal:invoke',
        endpoint = ENDPOINT,
        publicKey = PUBLIC_KEY,
        body = {
            eventId = '1',
            eventName = 'Downed',
            triggerActions = { 'SaveClip' },
            clipOptions = {
                duration = 30,
                captureDelayMs = 0
            },
            contextTags = {
                framework = 'qbcore'
            }
        }
    })
end
