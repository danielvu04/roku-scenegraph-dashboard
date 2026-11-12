sub init()
    m.top.functionName = "runLaunch"
end sub

sub runLaunch()
    req = CreateObject("roUrlTransfer")
    req.SetUrl(m.top.bridgeUrl + "/api/launch/" + m.top.appId)
    req.SetRequest("POST")
    code = req.PostFromString("")
    print "LaunchTask: bridge launch "; m.top.appId; " status="; code
    m.top.result = code.ToStr()
end sub
