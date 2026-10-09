Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$gatewayClient = Join-Path $root 'src\localbrain_client.py'
$script:messages = [System.Collections.Generic.List[object]]::new()

$form = [Windows.Forms.Form]@{
    Text = 'LocalBrain'
    Size = [Drawing.Size]::new(1020, 760)
    StartPosition = 'CenterScreen'
    MinimumSize = [Drawing.Size]::new(850, 650)
}
$form.Font = [Drawing.Font]::new('Yu Gothic UI', 10)

$top = [Windows.Forms.FlowLayoutPanel]@{
    Dock = 'Top'
    Height = 48
    Padding = [Windows.Forms.Padding]::new(8)
    WrapContents = $false
}
$start = [Windows.Forms.Button]@{ Text = 'モデル起動'; Width = 110; Height = 30 }
$stop = [Windows.Forms.Button]@{ Text = 'モデル停止'; Width = 110; Height = 30 }
$refresh = [Windows.Forms.Button]@{ Text = '状態更新'; Width = 95; Height = 30 }
$openCodex = [Windows.Forms.Button]@{ Text = '運用手順'; Width = 120; Height = 30 }
$status = [Windows.Forms.Label]@{ Text = '状態を確認してください'; AutoSize = $true; Padding = [Windows.Forms.Padding]::new(12, 6, 0, 0) }
$top.Controls.AddRange(@($start, $stop, $refresh, $openCodex, $status))

$tabs = [Windows.Forms.TabControl]@{ Dock = 'Fill' }
$chatTab = [Windows.Forms.TabPage]@{ Text = 'ローカルLLMチャット' }
$searchTab = [Windows.Forms.TabPage]@{ Text = '第二の脳を検索' }
$logTab = [Windows.Forms.TabPage]@{ Text = '操作ログ' }
$tabs.TabPages.AddRange(@($chatTab, $searchTab, $logTab))

$chatOutput = [Windows.Forms.RichTextBox]@{
    Dock = 'Fill'
    ReadOnly = $true
    BackColor = [Drawing.Color]::White
    DetectUrls = $true
}
$chatBottom = [Windows.Forms.Panel]@{ Dock = 'Bottom'; Height = 115; Padding = [Windows.Forms.Padding]::new(8) }
$chatInput = [Windows.Forms.TextBox]@{
    Multiline = $true
    ScrollBars = 'Vertical'
    Dock = 'Fill'
    AcceptsReturn = $true
}
$send = [Windows.Forms.Button]@{ Text = '送信'; Dock = 'Right'; Width = 90 }
$clear = [Windows.Forms.Button]@{ Text = '履歴消去'; Dock = 'Right'; Width = 90 }
$chatBottom.Controls.Add($chatInput)
$chatBottom.Controls.Add($clear)
$chatBottom.Controls.Add($send)
$chatTab.Controls.Add($chatOutput)
$chatTab.Controls.Add($chatBottom)

$searchOutput = [Windows.Forms.RichTextBox]@{
    Dock = 'Fill'
    ReadOnly = $true
    BackColor = [Drawing.Color]::White
}
$searchTop = [Windows.Forms.FlowLayoutPanel]@{ Dock = 'Top'; Height = 50; Padding = [Windows.Forms.Padding]::new(8); WrapContents = $false }
$query = [Windows.Forms.TextBox]@{ Width = 430; PlaceholderText = '検索語' }
$project = [Windows.Forms.ComboBox]@{ Width = 170; DropDownStyle = 'DropDown' }
$null = $project.Items.AddRange(@('', 'chatgpt', 'chatgpt-logs', 'localbrain', 'integration', 'general'))
$project.Text = ''
$search = [Windows.Forms.Button]@{ Text = '検索'; Width = 90; Height = 28 }
$searchTop.Controls.AddRange(@($query, $project, $search))
$searchTab.Controls.Add($searchOutput)
$searchTab.Controls.Add($searchTop)

$operationLog = [Windows.Forms.RichTextBox]@{
    Dock = 'Fill'
    ReadOnly = $true
    BackColor = [Drawing.Color]::White
}
$logTab.Controls.Add($operationLog)
$form.Controls.Add($tabs)
$form.Controls.Add($top)

function Add-Log([string]$text) {
    $operationLog.AppendText(('[{0:HH:mm:ss}] {1}' -f (Get-Date), $text.Trim()) + [Environment]::NewLine)
    $operationLog.ScrollToCaret()
}

function Invoke-Gateway([string]$method, [string]$path, $body = $null) {
    $temporary = $null
    try {
        $arguments = @($gatewayClient, $method, $path)
        if ($null -ne $body) {
            $temporary = Join-Path ([IO.Path]::GetTempPath()) ('localbrain-gui-' + [guid]::NewGuid().ToString('N') + '.json')
            $body | ConvertTo-Json -Depth 20 -Compress | Set-Content -LiteralPath $temporary -Encoding utf8NoBOM
            $arguments += @('--body-file', $temporary)
        }
        $output = & $python @arguments 2>&1
        if ($LASTEXITCODE -ne 0) { throw ($output | Out-String) }
        return ($output | Out-String | ConvertFrom-Json)
    } finally {
        if ($temporary -and (Test-Path -LiteralPath $temporary)) { Remove-Item -LiteralPath $temporary -Force }
    }
}

function Get-ServiceStatus {
    try {
        $state = Invoke-Gateway GET '/v1/control/status'
        $status.Text = "Gateway: $($state.gateway) / Model: $($state.state) / Brain: $($state.brain)"
        $status.ForeColor = if ($state.gateway -eq 'ok' -and $state.brain) { [Drawing.Color]::DarkGreen } else { [Drawing.Color]::DarkOrange }
        Add-Log ($state | ConvertTo-Json -Depth 10 -Compress)
    } catch {
        $status.Text = '状態取得エラー'
        Add-Log $_.Exception.Message
    }
}

$start.Add_Click({
    try {
        $form.UseWaitCursor = $true
        Add-Log ((Invoke-Gateway POST '/v1/control/start' @{}) | ConvertTo-Json -Compress)
    } catch { Add-Log $_.Exception.Message }
    finally { $form.UseWaitCursor = $false; Get-ServiceStatus }
})

$stop.Add_Click({
    try {
        Add-Log ((Invoke-Gateway POST '/v1/control/stop' @{}) | ConvertTo-Json -Compress)
    } catch { Add-Log $_.Exception.Message }
    finally { Get-ServiceStatus }
})

$refresh.Add_Click({ Get-ServiceStatus })

$openCodex.Add_Click({
    Start-Process -FilePath (Join-Path $root 'OPERATIONS.md')
})

$send.Add_Click({
    $prompt = $chatInput.Text.Trim()
    if (-not $prompt) { return }
    try {
        $send.Enabled = $false
        $form.UseWaitCursor = $true
        $script:messages.Add([pscustomobject]@{ role = 'user'; content = $prompt })
        $recent = @($script:messages | Select-Object -Last 8)
        $body = @{ model = 'local-qwen38'; messages = $recent; max_tokens = 2048; temperature = 0.2 }
        $result = Invoke-Gateway POST '/v1/chat/completions' $body
        $answer = ([string]$result.choices[0].message.content).Trim()
        if (-not $answer) { $answer = '[応答本文がありません]' }
        $script:messages.Add([pscustomobject]@{ role = 'assistant'; content = $answer })
        $chatOutput.AppendText("あなた:`r`n$prompt`r`n`r`nLocalBrain:`r`n$answer`r`n`r`n")
        $chatOutput.ScrollToCaret()
        $chatInput.Clear()
    } catch {
        [Windows.Forms.MessageBox]::Show("送信に失敗しました。`r`n$($_.Exception.Message)", 'LocalBrain', 'OK', 'Error')
        Add-Log $_.Exception.Message
    } finally {
        $send.Enabled = $true
        $form.UseWaitCursor = $false
    }
})

$clear.Add_Click({
    $script:messages.Clear()
    $chatOutput.Clear()
})

$search.Add_Click({
    if (-not $query.Text.Trim()) { return }
    try {
        $body = @{ query = $query.Text.Trim(); limit = 10 }
        if ($project.Text.Trim()) { $body.project = $project.Text.Trim() }
        $searchOutput.Text = ((Invoke-Gateway POST '/v1/brain/search' $body) | ConvertTo-Json -Depth 20)
    } catch {
        $searchOutput.Text = $_.Exception.Message
    }
})

$form.Add_Shown({ Get-ServiceStatus })
[void]$form.ShowDialog()
