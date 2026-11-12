' HomeScene - render-thread logic only; network work lives in WeatherTask.

sub init()
    m.cfg = GetCouchConfig()
    m.deg = Chr(176)
    m.dayNames = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    m.monthNames = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]

    m.info = m.top.findNode("info")
    m.clock = m.top.findNode("clock")
    m.ampm = m.top.findNode("ampm")
    m.ampmShadow = m.top.findNode("ampmShadow")
    m.weather = m.top.findNode("weather")
    m.wxIcon = m.top.findNode("wxIcon")

    ' --- clock -----------------------------------------------------------
    m.lastClockText = ""
    m.lastDateText = ""
    updateClock()
    m.clockTimer = m.top.findNode("clockTimer")
    m.clockTimer.observeField("fire", "updateClock")
    m.clockTimer.control = "start"

    ' --- backgrounds -----------------------------------------------------
    m.bgA = m.top.findNode("bgA")
    m.bgB = m.top.findNode("bgB")
    m.fadeAnim = m.top.findNode("fadeAnim")
    m.fadeInterp = m.top.findNode("fadeInterp")
    m.bgLoadTimeout = m.top.findNode("bgLoadTimeout")
    m.bgA.observeField("loadStatus", "onBgLoadStatus")
    m.bgB.observeField("loadStatus", "onBgLoadStatus")
    m.bgLoadTimeout.observeField("fire", "onBgLoadTimeout")
    m.backgrounds = m.cfg.backgrounds
    m.bgIndex = 0
    m.topVisible = false ' true when bgB is the poster on screen
    m.pendingPoster = invalid
    if m.backgrounds.Count() > 0
        m.bgIndex = Rnd(m.backgrounds.Count()) - 1
        m.bgA.uri = m.backgrounds[m.bgIndex]
    end if
    m.bgTimer = m.top.findNode("bgTimer")
    m.bgTimer.duration = Csng(m.cfg.backgroundSeconds)
    m.bgTimer.observeField("fire", "onBgTimer")
    if m.backgrounds.Count() > 1 then m.bgTimer.control = "start"

    ' --- burn-in drift ---------------------------------------------------
    m.driftInterp = m.top.findNode("driftInterp")
    m.driftAnim = m.top.findNode("driftAnim")
    m.driftTimer = m.top.findNode("driftTimer")
    m.driftTimer.observeField("fire", "onDrift")
    m.driftTimer.control = "start"

    ' --- weather ---------------------------------------------------------
    setLabel("place", m.cfg.locationName)
    m.weatherTask = invalid
    m.lastWeatherOk = invalid ' roTimespan since last good fetch
    m.retryDelay = 30
    m.weatherTimer = m.top.findNode("weatherTimer")
    m.weatherTimer.observeField("fire", "startWeatherFetch")
    startWeatherFetch()

    m.top.setFocus(true)
    m.top.signalBeacon("AppLaunchComplete")
end sub

' Sets a label and its drop-shadow twin ("<id>Shadow").
sub setLabel(id as String, text as String)
    m.top.findNode(id).text = text
    shadow = m.top.findNode(id + "Shadow")
    if shadow <> invalid then shadow.text = text
end sub

' ===================================================================== clock

sub updateClock()
    now = CreateObject("roDateTime")
    now.ToLocalTime()

    hours = now.GetHours()
    minutes = now.GetMinutes()
    mm = minutes.ToStr()
    if minutes < 10 then mm = "0" + mm

    suffix = ""
    if m.cfg.clock24h
        hh = hours.ToStr()
        if hours < 10 then hh = "0" + hh
    else
        suffix = "AM"
        if hours >= 12 then suffix = "PM"
        h12 = hours mod 12
        if h12 = 0 then h12 = 12
        hh = h12.ToStr()
    end if
    clockText = hh + ":" + mm

    if clockText <> m.lastClockText
        m.lastClockText = clockText
        setLabel("clock", clockText)
        setLabel("ampm", suffix)
        ' Place AM/PM as a superscript just right of the digits.
        x = m.clock.boundingRect().width + 18
        m.ampm.translation = [x, 30]
        m.ampmShadow.translation = [x + 3, 34]
    end if

    dateText = m.dayNames[now.GetDayOfWeek()] + ", " + m.monthNames[now.GetMonth() - 1] + " " + now.GetDayOfMonth().ToStr()
    if dateText <> m.lastDateText
        m.lastDateText = dateText
        setLabel("date", dateText)
    end if
end sub

' =============================================================== backgrounds

sub onBgTimer()
    showBackground(1)
end sub

' Load the next/previous image into the hidden poster; fade once it's ready.
sub showBackground(stepBy as Integer)
    n = m.backgrounds.Count()
    if n < 2 or m.pendingPoster <> invalid or m.fadeAnim.state = "running" then return
    m.bgIndex = (m.bgIndex + stepBy + n) mod n

    if m.topVisible
        m.pendingPoster = m.bgA
    else
        m.pendingPoster = m.bgB
    end if
    ' Clear first so loadStatus is guaranteed to change even for cached textures.
    m.pendingPoster.uri = ""
    m.pendingPoster.uri = m.backgrounds[m.bgIndex]
    m.bgLoadTimeout.control = "stop"
    m.bgLoadTimeout.control = "start"
end sub

sub onBgLoadStatus(event as Object)
    poster = event.getRoSGNode()
    if m.pendingPoster = invalid or not poster.isSameNode(m.pendingPoster) then return
    status = event.getData()
    if status = "ready"
        startCrossfade()
    else if status = "failed"
        print "HomeScene: failed to load "; poster.uri
        m.pendingPoster = invalid
        m.bgLoadTimeout.control = "stop"
    end if
end sub

' Safety net in case a loadStatus change is never observed.
sub onBgLoadTimeout()
    if m.pendingPoster <> invalid then startCrossfade()
end sub

sub startCrossfade()
    m.bgLoadTimeout.control = "stop"
    m.pendingPoster = invalid
    if m.topVisible
        m.fadeInterp.keyValue = [1.0, 0.0] ' reveal bgA underneath
    else
        m.fadeInterp.keyValue = [0.0, 1.0] ' bring bgB in over bgA
    end if
    m.topVisible = not m.topVisible
    m.fadeAnim.control = "start"
end sub

' ===================================================================== drift

' Move the clock/weather block to a new random spot inside a safe box.
sub onDrift()
    if m.driftAnim.state = "running" then return
    current = m.info.translation
    target = [100 + Rnd(220), 330 + Rnd(110)]
    m.driftInterp.keyValue = [current, target]
    m.driftAnim.control = "start"
end sub

' =================================================================== weather

sub startWeatherFetch()
    if m.weatherTask <> invalid then return ' previous run still in flight
    task = CreateObject("roSGNode", "WeatherTask")
    task.lat = m.cfg.lat
    task.lon = m.cfg.lon
    task.units = m.cfg.units
    task.observeField("result", "onWeatherResult")
    m.weatherTask = task
    task.control = "RUN"
end sub

sub onWeatherResult(event as Object)
    res = event.getData()
    if m.weatherTask <> invalid
        m.weatherTask.unobserveField("result")
        m.weatherTask = invalid
    end if

    if type(res) = "roAssociativeArray" and res.DoesExist("ok") and res.ok = true
        showWeather(res)
        m.lastWeatherOk = CreateObject("roTimespan")
        m.retryDelay = 30
        scheduleWeather(900)
    else
        if type(res) = "roAssociativeArray" then print "HomeScene: weather failed ("; res.error; ")"
        ' Keep showing the last reading for up to an hour, then hide it.
        if m.lastWeatherOk = invalid or m.lastWeatherOk.TotalSeconds() > 3600
            m.weather.visible = false
        end if
        scheduleWeather(m.retryDelay)
        m.retryDelay = m.retryDelay * 2
        if m.retryDelay > 900 then m.retryDelay = 900
    end if
end sub

sub scheduleWeather(seconds as Integer)
    m.weatherTimer.control = "stop"
    m.weatherTimer.duration = Csng(seconds)
    m.weatherTimer.control = "start"
end sub

sub showWeather(res as Object)
    setLabel("temp", res.temp.ToStr() + m.deg)
    line = res.text
    if res.hi <> invalid and res.lo <> invalid
        hilo = "H " + res.hi.ToStr() + m.deg + "  L " + res.lo.ToStr() + m.deg
        if line = ""
            line = hilo
        else
            line = line + "   " + hilo
        end if
    end if
    setLabel("wxText", line)
    m.wxIcon.uri = "pkg:/images/wx_" + res.icon + ".png"
    m.weather.visible = true
end sub

' ====================================================================== keys

function onKeyEvent(key as String, press as Boolean) as Boolean
    if not press then return false
    if key = "back"
        m.top.exitRequested = true
        return true
    else if key = "right" or key = "fastforward"
        showBackground(1)
        return true
    else if key = "left" or key = "rewind"
        showBackground(-1)
        return true
    else if key = "OK" or key = "replay"
        startWeatherFetch()
        return true
    end if
    return false
end function
