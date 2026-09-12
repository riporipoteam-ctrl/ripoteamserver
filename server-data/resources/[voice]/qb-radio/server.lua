local QBCore = exports['qb-core']:GetCoreObject()

QBCore.Functions.CreateUseableItem("radio", function(source)
    TriggerClientEvent('qb-radio:use', source)
end)

local function HasChannelAccess(config, Player)
    if not config then return true end -- No restrictions = open channel

    -- Check job access (requires onduty)
    if config.jobs then
        for _, job in ipairs(config.jobs) do
            if Player.PlayerData.job.name == job and Player.PlayerData.job.onduty then
                return true
            end
        end
    end

    -- Check gang access
    if config.gangs then
        for _, gang in ipairs(config.gangs) do
            if Player.PlayerData.gang and Player.PlayerData.gang.name == gang then
                return true
            end
        end
    end

    return false
end

for channel, config in pairs(Config.RestrictedChannels) do
    exports['pma-voice']:addChannelCheck(channel, function(source)
        local Player = QBCore.Functions.GetPlayer(source)
        return HasChannelAccess(config, Player)
    end)
end
