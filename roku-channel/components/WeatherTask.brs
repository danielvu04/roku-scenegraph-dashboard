' WeatherTask - runs off the render thread, so blocking I/O is fine here.

sub init()
    m.top.functionName = "fetchWeather"
end sub

sub fetchWeather()
    units = "fahrenheit"
    if m.top.units = "celsius" then units = "celsius"

    url = "https://api.open-meteo.com/v1/forecast"
    url = url + "?latitude=" + coordToStr(m.top.lat) + "&longitude=" + coordToStr(m.top.lon)
    url = url + "&current=temperature_2m,weather_code,is_day"
    url = url + "&daily=temperature_2m_max,temperature_2m_min"
    url = url + "&temperature_unit=" + units + "&timezone=auto&forecast_days=1"

    body = httpGet(url, 15000)
    if body = invalid
        m.top.result = { ok: false, error: "network" }
        return
    end if

    data = ParseJson(body)
    if type(data) <> "roAssociativeArray" or type(data.current) <> "roAssociativeArray"
        m.top.result = { ok: false, error: "parse" }
        return
    end if

    temp = data.current.temperature_2m
    code = data.current.weather_code
    if not isNumber(temp) or not isNumber(code)
        m.top.result = { ok: false, error: "fields" }
        return
    end if

    isDay = true
    if isNumber(data.current.is_day) then isDay = (data.current.is_day <> 0)

    hi = invalid
    lo = invalid
    if type(data.daily) = "roAssociativeArray"
        hi = firstNumber(data.daily.temperature_2m_max)
        lo = firstNumber(data.daily.temperature_2m_min)
    end if

    info = wmoInfo(Int(code), isDay)
    m.top.result = {
        ok: true
        temp: Cint(temp)
        hi: hi
        lo: lo
        code: Int(code)
        text: info.text
        icon: info.icon
    }
end sub

' Async GET with a timeout; returns the body string or invalid.
function httpGet(url as String, timeoutMs as Integer) as Dynamic
    port = CreateObject("roMessagePort")
    xfer = CreateObject("roUrlTransfer")
    xfer.SetMessagePort(port)
    xfer.SetUrl(url)
    xfer.SetCertificatesFile("common:/certs/ca-bundle.crt")
    xfer.InitClientCertificates()
    xfer.EnableEncodings(true)
    xfer.AddHeader("Accept", "application/json")

    if not xfer.AsyncGetToString() then return invalid
    msg = wait(timeoutMs, port)
    if type(msg) = "roUrlEvent"
        if msg.GetResponseCode() = 200 then return msg.GetString()
        print "WeatherTask: HTTP "; msg.GetResponseCode(); " "; msg.GetFailureReason()
        return invalid
    end if
    print "WeatherTask: timeout"
    xfer.AsyncCancel()
    return invalid
end function

function isNumber(v as Dynamic) as Boolean
    t = type(v)
    return t = "Integer" or t = "roInteger" or t = "Float" or t = "roFloat" or t = "Double" or t = "roDouble" or t = "LongInteger" or t = "roLongInteger"
end function

function firstNumber(arr as Dynamic) as Dynamic
    if type(arr) = "roArray" and arr.Count() > 0 and isNumber(arr[0]) then return Cint(arr[0])
    return invalid
end function

' Fixed 4-decimal string; Str() can emit exponent notation for small floats.
function coordToStr(v as Float) as String
    neg = v < 0
    scaled = Cint(Abs(v) * 10000)
    whole = scaled \ 10000
    frac = (scaled mod 10000).ToStr()
    while Len(frac) < 4
        frac = "0" + frac
    end while
    s = whole.ToStr() + "." + frac
    if neg then s = "-" + s
    return s
end function

' WMO weather interpretation codes (Open-Meteo docs) -> label + icon key.
function wmoInfo(code as Integer, isDay as Boolean) as Object
    sky = "night"
    if isDay then sky = "day"
    if code = 0 then return { text: "Clear", icon: "clear_" + sky }
    if code = 1 then return { text: "Mostly clear", icon: "clear_" + sky }
    if code = 2 then return { text: "Partly cloudy", icon: "partly_" + sky }
    if code = 3 then return { text: "Overcast", icon: "cloudy" }
    if code = 45 then return { text: "Fog", icon: "fog" }
    if code = 48 then return { text: "Freezing fog", icon: "fog" }
    if code = 51 then return { text: "Light drizzle", icon: "drizzle" }
    if code = 53 then return { text: "Drizzle", icon: "drizzle" }
    if code = 55 then return { text: "Heavy drizzle", icon: "drizzle" }
    if code = 56 or code = 57 then return { text: "Freezing drizzle", icon: "drizzle" }
    if code = 61 then return { text: "Light rain", icon: "rain" }
    if code = 63 then return { text: "Rain", icon: "rain" }
    if code = 65 then return { text: "Heavy rain", icon: "rain" }
    if code = 66 or code = 67 then return { text: "Freezing rain", icon: "rain" }
    if code = 71 then return { text: "Light snow", icon: "snow" }
    if code = 73 then return { text: "Snow", icon: "snow" }
    if code = 75 then return { text: "Heavy snow", icon: "snow" }
    if code = 77 then return { text: "Snow grains", icon: "snow" }
    if code = 80 then return { text: "Light showers", icon: "rain" }
    if code = 81 then return { text: "Showers", icon: "rain" }
    if code = 82 then return { text: "Heavy showers", icon: "rain" }
    if code = 85 then return { text: "Snow showers", icon: "snow" }
    if code = 86 then return { text: "Heavy snow showers", icon: "snow" }
    if code = 95 then return { text: "Thunderstorm", icon: "storm" }
    if code = 96 or code = 99 then return { text: "Thunderstorm, hail", icon: "storm" }
    return { text: "", icon: "cloudy" }
end function
