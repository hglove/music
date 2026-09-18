param(
    [string]$Action = "poll",
    [double]$Seconds = 0,
    [double]$Duration = 0
)

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$OutputEncoding = [Console]::OutputEncoding

Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties, Windows.Media.Control, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStreamWithContentType, Windows.Storage.Streams, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType = WindowsRuntime]
$script:TSessionMgr = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
$script:TMediaProps = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties, Windows.Media.Control, ContentType = WindowsRuntime]
$script:TStreamType = [Windows.Storage.Streams.IRandomAccessStreamWithContentType, Windows.Storage.Streams, ContentType = WindowsRuntime]
$script:TUInt32 = [System.UInt32]

# PowerShell 5.1: WinRT async ops arrive as opaque System.__ComObject, so PS cannot
# infer the AsTask type argument. Caller must supply the expected result type.
$script:AsTaskOpMethod = $null
function Get-AsTaskOpMethod {
    if ($null -ne $script:AsTaskOpMethod) { return $script:AsTaskOpMethod }
    try {
        Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
        $ext = [System.WindowsRuntimeSystemExtensions]
        foreach ($m in $ext.GetMethods()) {
            if ($m.Name -ne 'AsTask') { continue }
            $ps = $m.GetParameters()
            if ($ps.Count -ne 1) { continue }
            if (-not $ps[0].ParameterType.IsGenericType) { continue }
            if ($ps[0].ParameterType.GetGenericTypeDefinition().Name -notlike 'IAsyncOperation*') { continue }
            $script:AsTaskOpMethod = $m
            break
        }
    } catch {
        $script:AsTaskOpMethod = $null
    }
    return $script:AsTaskOpMethod
}

function Await-WinRt {
    # $1 = the IAsyncOperation COM object, $2 = the closed result Type
    param($Operation, [Type]$ResultType)
    if ($null -eq $Operation -or $null -eq $ResultType) { return $null }
    $method = Get-AsTaskOpMethod
    if ($null -eq $method) { return $null }
    try {
        $generic = $method.MakeGenericMethod($ResultType)
        $opArgs = New-Object object[] 1
        $opArgs[0] = $Operation
        $task = $generic.Invoke($null, $opArgs)
    } catch {
        return $null
    }
    if ($null -eq $task) { return $null }
    $null = $task.Wait(8000)
    if (-not $task.IsCompleted) { return $null }
    if ($task.IsFaulted) { return $null }
    try { return $task.Result } catch { return $null }
}

function Await-WinRtAction {
    param($Operation)
    if ($null -eq $Operation) { return }
    # IAsyncAction has a non-generic AsTask overload, call it directly
    try {
        $task = [System.WindowsRuntimeSystemExtensions]::AsTask($Operation)
    } catch {
        return
    }
    if ($null -eq $task) { return }
    $null = $task.Wait(8000)
}

function Get-Manager {
    return Await-WinRt ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) $script:TSessionMgr
}

function Get-ThumbB64 {
    param($Props)
    try {
        if ($null -eq $Props -or $null -eq $Props.Thumbnail) { return "" }
        $stream = Await-WinRt ($Props.Thumbnail.OpenReadAsync()) $script:TStreamType
        if ($null -eq $stream) { return "" }
        $size = [int]$stream.Size
        if ($size -le 0 -or $size -gt 4000000) { return "" }
        $reader = [Windows.Storage.Streams.DataReader]::Create($stream)
        $null = Await-WinRt ($reader.LoadAsync([uint32]$size)) $script:TUInt32
        $bytes = New-Object byte[] $size
        $reader.ReadBytes($bytes)
        return "data:image/jpeg;base64," + [Convert]::ToBase64String($bytes)
    } catch {
        return ""
    }
}

function Get-ActiveSession {
    # Prefer a session that is actually playing; fall back to the system's current one.
    param($Mgr)
    $current = $null
    try { $current = $Mgr.GetCurrentSession() } catch { $current = $null }
    if ($null -ne $current) {
        try {
            if ([string]$current.GetPlaybackInfo().PlaybackStatus -eq "Playing") { return $current }
        } catch {}
    }
    $sessions = $null
    try { $sessions = $Mgr.GetSessions() } catch { $sessions = $null }
    if ($null -eq $sessions) { return $current }
    $fallback = $current
    foreach ($s in $sessions) {
        $status = ""
        try { $status = [string]$s.GetPlaybackInfo().PlaybackStatus } catch { continue }
        if ($status -eq "Playing") { return $s }
        if ($null -eq $fallback) { $fallback = $s }
    }
    return $fallback
}

# Players that never register with SMTC (网易云音乐 does not on every version) still
# expose the current track in their window title, so fall back to that.
$script:FallbackProcess = "cloudmusic"
$script:FallbackWindowClasses = @("OrpheusBrowserHost", "icon")
$script:NativeSource = @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public static class SmtcFallback
{
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr FindWindowW(string className, string windowName);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr FindWindowExW(IntPtr parent, IntPtr child, string className, string windowName);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowTextW(IntPtr hWnd, StringBuilder text, int maxCount);
    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [DllImport("user32.dll")]
    private static extern void keybd_event(byte virtualKey, byte scanCode, uint flags, UIntPtr extraInfo);

    private const uint KEYEVENTF_EXTENDEDKEY = 0x0001;
    private const uint KEYEVENTF_KEYUP = 0x0002;

    // The shell routes media keys to the player, so this still works for a player that
    // never publishes an SMTC session of its own.
    public static void SendMediaKey(int virtualKey)
    {
        keybd_event((byte)virtualKey, 0, KEYEVENTF_EXTENDEDKEY, UIntPtr.Zero);
        keybd_event((byte)virtualKey, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, UIntPtr.Zero);
    }

    public static string FindWindowTitle(string processName, string[] classNames)
    {
        var pids = new HashSet<uint>();
        foreach (var process in System.Diagnostics.Process.GetProcessesByName(processName))
        {
            pids.Add((uint)process.Id);
            process.Dispose();
        }
        if (pids.Count == 0) { return ""; }
        for (int i = 0; i < classNames.Length; i++)
        {
            IntPtr hWnd = FindWindowW(classNames[i], null);
            while (hWnd != IntPtr.Zero)
            {
                uint pid;
                GetWindowThreadProcessId(hWnd, out pid);
                if (pids.Contains(pid))
                {
                    var titleBuf = new StringBuilder(512);
                    GetWindowTextW(hWnd, titleBuf, titleBuf.Capacity);
                    string title = titleBuf.ToString();
                    if (title.Length > 0) { return title; }
                }
                hWnd = FindWindowExW(IntPtr.Zero, hWnd, classNames[i], null);
            }
        }
        return "";
    }

    [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
    private class MMDeviceEnumerator { }

    [Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IMMDeviceEnumerator
    {
        int EnumAudioEndpoints(int dataFlow, int stateMask, out IntPtr devices);
        int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice device);
    }

    [Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IMMDevice
    {
        int Activate(ref Guid iid, int clsCtx, IntPtr activationParams, [MarshalAs(UnmanagedType.IUnknown)] out object iface);
    }

    [Guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IAudioSessionManager2
    {
        int GetAudioSessionControl(ref Guid sessionGuid, int streamFlags, out IntPtr sessionControl);
        int GetSimpleAudioVolume(ref Guid sessionGuid, int streamFlags, out IntPtr audioVolume);
        int GetSessionEnumerator(out IAudioSessionEnumerator sessionEnum);
    }

    [Guid("E2F5BB11-0570-40CA-ACDD-3AA01277DEE8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IAudioSessionEnumerator
    {
        int GetCount(out int sessionCount);
        int GetSession(int index, out IAudioSessionControl2 session);
    }

    [Guid("BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IAudioSessionControl2
    {
        int GetState(out int state);
        int GetDisplayName(out IntPtr name);
        int SetDisplayName(string name, ref Guid eventContext);
        int GetIconPath(out IntPtr path);
        int SetIconPath(string path, ref Guid eventContext);
        int GetGroupingParam(out Guid param);
        int SetGroupingParam(ref Guid param, ref Guid eventContext);
        int RegisterAudioSessionNotification(IntPtr notify);
        int UnregisterAudioSessionNotification(IntPtr notify);
        int GetSessionIdentifier(out IntPtr id);
        int GetSessionInstanceIdentifier(out IntPtr id);
        int GetProcessId(out uint pid);
        int IsSystemSoundsSession();
        int SetDuckingPreference(bool optOut);
    }

    public static bool IsProcessRenderingAudio(string processName)
    {
        try
        {
            var enumerator = (IMMDeviceEnumerator)(new MMDeviceEnumerator());
            IMMDevice device;
            if (enumerator.GetDefaultAudioEndpoint(0, 1, out device) != 0) { return false; }
            Guid iid = typeof(IAudioSessionManager2).GUID;
            object raw;
            if (device.Activate(ref iid, 1, IntPtr.Zero, out raw) != 0) { return false; }
            IAudioSessionEnumerator sessions;
            if (((IAudioSessionManager2)raw).GetSessionEnumerator(out sessions) != 0) { return false; }
            int count;
            sessions.GetCount(out count);
            for (int i = 0; i < count; i++)
            {
                IAudioSessionControl2 control;
                if (sessions.GetSession(i, out control) != 0) { continue; }
                int state;
                control.GetState(out state);
                if (state != 1) { continue; }
                uint pid;
                control.GetProcessId(out pid);
                string process;
                try { process = System.Diagnostics.Process.GetProcessById((int)pid).ProcessName; }
                catch { continue; }
                if (string.Equals(process, processName, StringComparison.OrdinalIgnoreCase)) { return true; }
            }
        }
        catch { }
        return false;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct RECT { public int Left; public int Top; public int Right; public int Bottom; }

    [StructLayout(LayoutKind.Sequential)]
    private struct POINT { public int X; public int Y; }

    [StructLayout(LayoutKind.Sequential)]
    private struct WINDOWPLACEMENT
    {
        public int length;
        public int flags;
        public int showCmd;
        public POINT minPosition;
        public POINT maxPosition;
        public RECT normalPosition;
    }

    private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);
    [DllImport("user32.dll")]
    private static extern bool EnumChildWindows(IntPtr parent, EnumWindowsProc callback, IntPtr lParam);
    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")]
    private static extern bool IsIconic(IntPtr hWnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetClassNameW(IntPtr hWnd, StringBuilder text, int maxCount);
    [DllImport("user32.dll")]
    private static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
    [DllImport("user32.dll")]
    private static extern bool GetWindowPlacement(IntPtr hWnd, ref WINDOWPLACEMENT placement);
    [DllImport("user32.dll")]
    private static extern bool ShowWindowAsync(IntPtr hWnd, int cmdShow);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern bool PostMessageW(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);

    private const uint WM_MOUSEMOVE = 0x0200;
    private const uint WM_LBUTTONDOWN = 0x0201;
    private const uint WM_LBUTTONUP = 0x0202;
    private const int SW_SHOWNOACTIVATE = 4;
    private const int SW_SHOWMINNOACTIVE = 7;
    // The progress bar sits this many pixels above the bottom edge of the player window.
    private const int SEEK_BAR_OFFSET_Y = 83;

    private static uint[] ProcessIds(string processName)
    {
        var list = new List<uint>();
        foreach (var process in System.Diagnostics.Process.GetProcessesByName(processName))
        {
            list.Add((uint)process.Id);
            process.Dispose();
        }
        return list.ToArray();
    }

    private static bool OwnedBy(IntPtr hWnd, uint[] pids)
    {
        uint pid;
        GetWindowThreadProcessId(hWnd, out pid);
        for (int i = 0; i < pids.Length; i++) { if (pids[i] == pid) { return true; } }
        return false;
    }

    private static string ClassName(IntPtr hWnd)
    {
        var buffer = new StringBuilder(128);
        GetClassNameW(hWnd, buffer, buffer.Capacity);
        return buffer.ToString();
    }

    private static IntPtr FindMainWindow(string processName, string windowClass)
    {
        uint[] pids = ProcessIds(processName);
        if (pids.Length == 0) { return IntPtr.Zero; }
        IntPtr found = IntPtr.Zero;
        EnumWindows(delegate(IntPtr hWnd, IntPtr lParam)
        {
            if (!OwnedBy(hWnd, pids)) { return true; }
            if (ClassName(hWnd) != windowClass) { return true; }
            var title = new StringBuilder(8);
            if (GetWindowTextW(hWnd, title, title.Capacity) <= 0) { return true; }
            found = hWnd;
            return false;
        }, IntPtr.Zero);
        return found;
    }

    // 网易云音乐 publishes no SMTC session and its Chromium UI exposes no accessibility
    // tree, so the only way to jump playback position is to click its progress bar.
    // Posting the mouse messages to the browser widget works without moving the cursor
    // or stealing focus, but only reaches the renderer while the window is visible; a
    // minimized window is therefore shown with SW_SHOWNOACTIVATE first.
    public static string SeekByRatio(string processName, string windowClass, string childClass, double ratio)
    {
        if (ratio < 0) { ratio = 0; }
        if (ratio > 1) { ratio = 1; }
        IntPtr root = FindMainWindow(processName, windowClass);
        if (root == IntPtr.Zero) { return "NO_WINDOW"; }

        int width = 0;
        int height = 0;
        var placement = new WINDOWPLACEMENT();
        placement.length = Marshal.SizeOf(typeof(WINDOWPLACEMENT));
        if (GetWindowPlacement(root, ref placement))
        {
            width = placement.normalPosition.Right - placement.normalPosition.Left;
            height = placement.normalPosition.Bottom - placement.normalPosition.Top;
        }

        bool wasIconic = IsIconic(root);
        if (wasIconic)
        {
            ShowWindowAsync(root, SW_SHOWNOACTIVATE);
            System.Threading.Thread.Sleep(500);
        }

        RECT rect;
        if (GetWindowRect(root, out rect))
        {
            width = rect.Right - rect.Left;
            height = rect.Bottom - rect.Top;
        }
        if (width <= 0 || height <= 0) { return "NO_GEOMETRY"; }

        IntPtr widget = IntPtr.Zero;
        EnumChildWindows(root, delegate(IntPtr child, IntPtr param)
        {
            if (ClassName(child) != childClass) { return true; }
            if (!IsWindowVisible(child)) { return true; }
            RECT childRect;
            GetWindowRect(child, out childRect);
            bool sizeMatch = (childRect.Right - childRect.Left) == width && (childRect.Bottom - childRect.Top) == height;
            if (sizeMatch || widget == IntPtr.Zero) { widget = child; }
            return true;
        }, IntPtr.Zero);
        if (widget == IntPtr.Zero)
        {
            if (wasIconic) { ShowWindowAsync(root, SW_SHOWMINNOACTIVE); }
            return "NO_WIDGET";
        }

        int x = (int)Math.Round(ratio * (width - 1));
        int y = height - SEEK_BAR_OFFSET_Y;
        if (y < 0) { y = 0; }
        IntPtr lParam = (IntPtr)((y << 16) | (x & 0xFFFF));
        PostMessageW(widget, WM_MOUSEMOVE, IntPtr.Zero, lParam);
        PostMessageW(widget, WM_LBUTTONDOWN, (IntPtr)1, lParam);
        PostMessageW(widget, WM_LBUTTONUP, IntPtr.Zero, lParam);
        System.Threading.Thread.Sleep(150);
        if (wasIconic) { ShowWindowAsync(root, SW_SHOWMINNOACTIVE); }
        return string.Format("CLICK x={0} y={1} ratio={2:0.###} size={3}x{4} wasIconic={5}", x, y, ratio, width, height, wasIconic);
    }
}
'@
$script:NativeReady = $false
$script:NativeFailed = $false

function Initialize-NativeTypes {
    if ($script:NativeReady -or $script:NativeFailed) { return }
    try {
        Add-Type -TypeDefinition $script:NativeSource -Language CSharp | Out-Null
        $script:NativeReady = $true
    } catch {
        $script:NativeFailed = $true
    }
}

function Get-FallbackSnapshot {
    Initialize-NativeTypes
    if (-not $script:NativeReady) { return $null }
    $title = ""
    try { $title = [SmtcFallback]::FindWindowTitle($script:FallbackProcess, $script:FallbackWindowClasses) } catch { $title = "" }
    $sep = $title.LastIndexOf(" - ")
    if ($sep -le 0) { return $null }
    $song = $title.Substring(0, $sep).Trim()
    $artist = $title.Substring($sep + 3).Trim()
    if (-not $song) { return $null }
    $status = "Paused"
    try {
        if ([SmtcFallback]::IsProcessRenderingAudio($script:FallbackProcess)) { $status = "Playing" }
    } catch {}
    return [ordered]@{
        session  = "cloudmusic.exe|window-title"
        title    = $song
        artist   = $artist
        album    = ""
        status   = $status
        duration = 0
        fallback = $true
    }
}

function Invoke-FallbackControl {
    param([string]$Name)
    $vk = switch ($Name) {
        "prev" { 0xB1 }        # VK_MEDIA_PREV_TRACK
        "next" { 0xB0 }        # VK_MEDIA_NEXT_TRACK
        "playpause" { 0xB3 }   # VK_MEDIA_PLAY_PAUSE
    }
    if ($null -eq $vk) { return }
    Initialize-NativeTypes
    if (-not $script:NativeReady) { return }
    $title = ""
    try { $title = [SmtcFallback]::FindWindowTitle($script:FallbackProcess, $script:FallbackWindowClasses) } catch { $title = "" }
    if (-not $title) { return }
    [SmtcFallback]::SendMediaKey([int]$vk)
}

function Get-Snapshot {
    param($Session, [bool]$WantThumb)
    if ($null -eq $Session) {
        return [ordered]@{
            session  = ""
            title    = ""
            artist   = ""
            album    = ""
            status   = "stopped"
            duration = 0
        }
    }
    $sid = ""
    try { $sid = [string]$Session.SourceAppUserModelId } catch { $sid = "" }
    $title = ""; $artist = ""; $album = ""; $thumb = $null
    $props = Await-WinRt ($Session.TryGetMediaPropertiesAsync()) $script:TMediaProps
    if ($null -ne $props) {
        try { $title = [string]$props.Title } catch {}
        try { $artist = [string]$props.Artist } catch {}
        try { $album = [string]$props.AlbumTitle } catch {}
        if ($WantThumb) { $thumb = Get-ThumbB64 $props }
    }
    $status = "unknown"
    try {
        $info = $Session.GetPlaybackInfo()
        if ($null -ne $info) { $status = [string]$info.PlaybackStatus }
    } catch {}
    $dur = 0
    try {
        $tl = $Session.GetTimelineProperties()
        if ($null -ne $tl) { $dur = [double]$tl.EndTime.TotalSeconds }
    } catch {}
    $obj = [ordered]@{
        session  = $sid
        title    = $title
        artist   = $artist
        album    = $album
        status   = $status
        duration = $dur
    }
    if ($WantThumb) { $obj.thumb = $thumb }
    return $obj
}

function Invoke-FallbackSeek {
    param([double]$Pos, [double]$Span)
    Initialize-NativeTypes
    if (-not $script:NativeReady) { return }
    if ($Span -le 0) { $Span = 240.0 }
    $result = ""
    try {
        $result = [SmtcFallback]::SeekByRatio($script:FallbackProcess, $script:FallbackWindowClasses[0], "Chrome_WidgetWin_0", ($Pos / $Span))
    } catch {
        $result = "ERROR " + $_.Exception.Message
    }
    Write-Output ("FALLBACK_SEEK " + $result)
}

function Invoke-Control {
    param($Mgr, [string]$Name, [double]$Pos, [double]$Span)
    $session = Get-ActiveSession $Mgr
    if ($null -eq $session) {
        # 网易云音乐 publishes no SMTC session, so Try*Async has nothing to act on. Its
        # player window still answers the global media keys and its progress bar accepts
        # posted mouse messages, which is how a seek is performed.
        if ($Name -eq "seek") { Invoke-FallbackSeek $Pos $Span; return }
        Invoke-FallbackControl $Name
        return
    }
    switch ($Name) {
        "prev" { Await-WinRtAction ($session.TrySkipPreviousAsync()) }
        "next" { Await-WinRtAction ($session.TrySkipNextAsync()) }
        "playpause" { Await-WinRtAction ($session.TryTogglePlayPauseAsync()) }
        "seek" { Await-WinRtAction ($session.TryChangePlaybackPositionAsync([TimeSpan]::FromSeconds($Pos))) }
    }
}

$mgr = Get-Manager
if ($null -eq $mgr) {
    if ($Action -eq "poll") {
        Write-Output '{"error":"SMTC unavailable"}'
    }
    exit 1
}

if ($Action -ne "poll") {
    Invoke-Control $mgr $Action $Seconds $Duration
    exit 0
}

$lastMedia = "___"
while ($true) {
    $wantThumb = $false
    $session = Get-ActiveSession $mgr
    $mediaKey = ""
    if ($null -ne $session) {
        $props = Await-WinRt ($session.TryGetMediaPropertiesAsync()) $script:TMediaProps
        if ($null -ne $props) {
            $mediaKey = ([string]$props.Title) + "|" + ([string]$props.Artist) + "|" + ([string]$props.AlbumTitle)
        }
    }
    if ($mediaKey -ne $lastMedia) {
        $lastMedia = $mediaKey
        $wantThumb = $true
    }
    $snap = Get-Snapshot $session $wantThumb
    if ($snap.session -eq "" -or $snap.status -ne "Playing") {
        # Nothing playing through SMTC — a player that does not publish an SMTC session
        # (网易云音乐) still shows its track in the window title.
        $fallback = Get-FallbackSnapshot
        if ($null -ne $fallback -and ($snap.session -eq "" -or $fallback.status -eq "Playing")) {
            $snap = $fallback
        }
    }
    Write-Output ($snap | ConvertTo-Json -Compress -Depth 4)
    [Console]::Out.Flush()
    Start-Sleep -Milliseconds 500
}
