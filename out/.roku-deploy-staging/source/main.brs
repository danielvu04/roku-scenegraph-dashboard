' Couch Dashboard - ambient clock / weather channel.
' Entry point: create the SceneGraph screen, show HomeScene and wait until
' the screen closes (Back on the home scene), then return to exit the channel.

sub Main()
    screen = CreateObject("roSGScreen")
    port = CreateObject("roMessagePort")
    screen.SetMessagePort(port)

    scene = screen.CreateScene("HomeScene")
    screen.Show()
    scene.observeField("exitRequested", port)

    while true
        msg = wait(0, port)
        msgType = type(msg)
        if msgType = "roSGScreenEvent"
            if msg.IsScreenClosed() then return
        else if msgType = "roSGNodeEvent"
            if msg.GetField() = "exitRequested" and msg.GetData() = true
                screen.Close()
            end if
        end if
    end while
end sub
