Config = {}

Config.RadioItem = 'radio'

--[[
    RestrictedChannels Configuration

    Supports both jobs and gangs for channel restrictions.
    Players with a matching job (when on duty) OR matching gang can access the channel.

    Format:
    [channel] = {
        jobs = { "job1", "job2" },           -- Optional: list of allowed jobs (requires onduty)
        gangs = { "gang1", "gang2" },        -- Optional: list of allowed gangs
    }

    Examples:
    - Job-only channel: [1] = { jobs = { "police", "ambulance" } }
    - Gang-only channel: [500] = { gangs = { "ballas", "vagos" } }
    - Mixed channel: [999] = { jobs = { "police" }, gangs = { "lostmc" } }
]]
Config.RestrictedChannels = {
    [1] = {
        jobs = { "police", "ambulance" }
    },
    [2] = {
        jobs = { "police", "ambulance" }
    },
    [3] = {
        jobs = { "police", "ambulance" }
    },
    [4] = {
        jobs = { "police", "ambulance" }
    },
    [5] = {
        jobs = { "police", "ambulance" }
    },
    [6] = {
        jobs = { "police", "ambulance" }
    },
    [7] = {
        jobs = { "police", "ambulance" }
    },
    [8] = {
        jobs = { "police", "ambulance" }
    },
    [9] = {
        jobs = { "police", "ambulance" }
    },
    [10] = {
        jobs = { "police", "ambulance" }
    }
}

Config.MaxFrequency = 500