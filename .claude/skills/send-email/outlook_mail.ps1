<#
  Outlook helper for the send-email skill. Uses the Outlook app on this computer.
  Nothing is sent unless -Action send is run with a draft's EntryId.

  -Action accounts                         list the mail accounts set up in Outlook
  -Action find   -Query "spencer"          look up a person: contacts, address book, recent sent mail
  -Action draft  -From <acct> -To <addr> -Subject <s> -BodyFile <path> [-Cc <addr>] [-Attach <paths>] [-EntryId <id>] [-Show] [-Discard]
                                           save (or update) a draft in that account's Drafts folder; prints its EntryId
                                           -Discard builds it without saving (test mode)
  -Action send   -EntryId <id>             send one saved draft
  -Action inbox  -Sender <addr> [-Since yyyy-MM-dd]
                                           list messages from one sender in every Inbox (read-only)
  -Action save   -EntryId <id> -OutDir <dir>
                                           copy one received message (as .txt) and its attachments into a new folder;
                                           never overwrites; marks nothing read
#>
param(
  [Parameter(Mandatory)][ValidateSet('accounts','find','draft','send','inbox','save')][string]$Action,
  [string]$Sender,
  [string]$Since,
  [string]$OutDir,
  [string]$Query,
  [string]$From,
  [string]$To,
  [string]$Cc,
  [string]$Subject,
  [string]$BodyFile,
  [string[]]$Attach,
  [string]$EntryId,
  [switch]$Show,
  [switch]$Discard
)
$ErrorActionPreference = 'Stop'

function Get-Outlook {
  try { return [Runtime.InteropServices.Marshal]::GetActiveObject('Outlook.Application') }
  catch { return New-Object -ComObject Outlook.Application }
}

$ol = Get-Outlook
$ns = $ol.Session

function Get-Account([string]$addr) {
  foreach ($a in $ns.Accounts) { if ($a.SmtpAddress -ieq $addr) { return $a } }
  $list = ($ns.Accounts | ForEach-Object { $_.SmtpAddress }) -join ', '
  throw "No Outlook account '$addr'. Accounts in Outlook: $list"
}

function Get-Smtp($recipient) {
  try {
    $ae = $recipient.AddressEntry
    if ($ae.Type -eq 'EX') {
      $u = $ae.GetExchangeUser(); if ($u) { return $u.PrimarySmtpAddress }
    }
    return $ae.Address
  } catch { return $recipient.Address }
}

switch ($Action) {

  'accounts' {
    foreach ($a in $ns.Accounts) { "$($a.SmtpAddress) | $($a.DisplayName)" }
  }

  'find' {
    if (-not $Query) { throw 'find needs -Query' }
    $q = $Query.ToLower()
    $hits = [ordered]@{}
    function Add-Hit($name, $email, $source) {
      if (-not $email) { return }
      $key = $email.ToLower()
      if (-not $hits.Contains($key)) { $hits[$key] = "$name | $email | $source" }
    }
    # 1. Contacts folder of every mailbox
    foreach ($store in $ns.Stores) {
      try { $folder = $store.GetDefaultFolder(10) } catch { continue }
      foreach ($c in $folder.Items) {
        if ($c.Class -ne 40) { continue }
        $text = "$($c.FullName) $($c.CompanyName) $($c.Email1Address) $($c.Email2Address) $($c.Email3Address)".ToLower()
        if ($text.Contains($q)) {
          foreach ($e in @($c.Email1Address, $c.Email2Address, $c.Email3Address)) {
            if ($e) { Add-Hit $c.FullName $e "contacts ($($store.DisplayName)); company: $($c.CompanyName); phone: $($c.BusinessTelephoneNumber)$($c.MobileTelephoneNumber)" }
          }
        }
      }
    }
    # 2. Address book (company directory, suggested contacts)
    try {
      $r = $ns.CreateRecipient($Query)
      if ($r.Resolve()) { Add-Hit $r.Name (Get-Smtp $r) 'address book' }
    } catch {}
    # 3. People recently emailed (last 300 sent items per mailbox)
    foreach ($store in $ns.Stores) {
      try { $sent = $store.GetDefaultFolder(5).Items } catch { continue }
      $sent.Sort('[SentOn]', $true)
      $n = 0
      foreach ($m in $sent) {
        if ($n++ -ge 300) { break }
        if ($m.Class -ne 43) { continue }
        foreach ($rc in $m.Recipients) {
          $addr = Get-Smtp $rc
          if ("$($rc.Name) $addr".ToLower().Contains($q)) { Add-Hit $rc.Name $addr "sent mail ($($store.DisplayName)), last $($m.SentOn.ToString('yyyy-MM-dd'))" }
        }
      }
    }
    if ($hits.Count -eq 0) { "No match for '$Query'." } else { $hits.Values }
  }

  'draft' {
    if ($EntryId) {
      $mail = $ns.GetItemFromID($EntryId)
      # Outlook does not report the account of a reopened draft; pin it to the mailbox the draft is in
      $storeId = $mail.Parent.Store.StoreID
      $acct = $null
      foreach ($a in $ns.Accounts) { if ($a.DeliveryStore.StoreID -eq $storeId) { $acct = $a } }
      if ($acct) { [void]$mail.GetType().InvokeMember('SendUsingAccount', [Reflection.BindingFlags]::SetProperty, $null, $mail, @($acct)) }
    } else {
      foreach ($p in 'From','To','Subject','BodyFile') { if (-not (Get-Variable $p -ValueOnly)) { throw "draft needs -$p" } }
      $acct = Get-Account $From
      $drafts = $acct.DeliveryStore.GetDefaultFolder(16)
      $mail = $drafts.Items.Add(0)
      # A plain assignment of a COM object property is silently ignored in PowerShell 5.1
      [void]$mail.GetType().InvokeMember('SendUsingAccount', [Reflection.BindingFlags]::SetProperty, $null, $mail, @($acct))
    }
    if ($To) { $mail.To = $To }
    if ($PSBoundParameters.ContainsKey('Cc')) { $mail.CC = $Cc }
    if ($Subject) { $mail.Subject = $Subject }
    if ($BodyFile) { $mail.Body = [IO.File]::ReadAllText((Resolve-Path $BodyFile), [Text.Encoding]::UTF8) }
    foreach ($f in $Attach) { if ($f) { [void]$mail.Attachments.Add((Resolve-Path $f).Path) } }
    # An unsaved item can report the wrong Parent (seen on the hotmail account), so name the folder we used
    $where = if ($drafts) { $drafts.FolderPath } else { $mail.Parent.FolderPath }
    "From: $($acct.SmtpAddress) (draft is in $where)"
    "To: $($mail.To)"
    if ($mail.CC) { "Cc: $($mail.CC)" }
    "Subject: $($mail.Subject)"
    "Attachments: $($mail.Attachments.Count)"
    if ($Discard) { $mail.Close(1); 'Test only: draft built and discarded, not saved.' }
    else {
      $mail.Save()
      "Saved to Drafts. EntryId: $($mail.EntryID)"
      if ($Show) { $mail.Display() }
    }
  }

  'inbox' {
    if (-not $Sender) { throw 'inbox needs -Sender' }
    $after = if ($Since) { [datetime]::ParseExact($Since, 'yyyy-MM-dd', $null) } else { (Get-Date).AddDays(-30) }
    $found = 0
    foreach ($store in $ns.Stores) {
      try { $items = $store.GetDefaultFolder(6).Items } catch { continue }
      $items = $items.Restrict("[ReceivedTime] >= '" + $after.ToString('MM/dd/yyyy HH:mm') + "'")
      foreach ($m in $items) {
        if ($m.Class -ne 43) { continue }
        $addr = $m.SenderEmailAddress
        if ($m.SenderEmailType -eq 'EX') { try { $addr = $m.Sender.GetExchangeUser().PrimarySmtpAddress } catch {} }
        if ($addr -ieq $Sender) {
          $found++
          "$($m.ReceivedTime.ToString('yyyy-MM-dd HH:mm')) | $($store.DisplayName) | $($m.Subject) | attachments: $($m.Attachments.Count) | EntryId: $($m.EntryID)"
        }
      }
    }
    if (-not $found) { "No messages from $Sender since $($after.ToString('yyyy-MM-dd'))." }
  }

  'save' {
    if (-not $EntryId -or -not $OutDir) { throw 'save needs -EntryId and -OutDir' }
    if (Test-Path $OutDir) { throw "Folder already exists, not overwriting: $OutDir" }
    $m = $ns.GetItemFromID($EntryId)
    $wasUnread = $m.UnRead
    [void](New-Item -ItemType Directory -Path $OutDir)
    $fromAddr = $m.SenderEmailAddress
    if ($m.SenderEmailType -eq 'EX') { try { $fromAddr = $m.Sender.GetExchangeUser().PrimarySmtpAddress } catch {} }
    $head = "From: $($m.SenderName) <$fromAddr>`r`nTo: $($m.To)`r`nReceived: $($m.ReceivedTime.ToString('yyyy-MM-dd HH:mm'))`r`nSubject: $($m.Subject)`r`n`r`n"
    [IO.File]::WriteAllText((Join-Path $OutDir 'message.txt'), $head + $m.Body, [Text.Encoding]::UTF8)
    "Saved message.txt"
    foreach ($a in $m.Attachments) {
      $name = ($a.FileName -replace '[\\/:*?"<>|]', '_')
      $path = Join-Path $OutDir $name
      if (Test-Path $path) { "Skipped duplicate name: $name"; continue }
      $a.SaveAsFile($path)
      "Saved attachment: $name ($([math]::Round((Get-Item $path).Length/1KB)) KB)"
    }
    if ($wasUnread -and -not $m.UnRead) { $m.UnRead = $true; $m.Save() }
  }

  'send' {
    if (-not $EntryId) { throw 'send needs -EntryId' }
    $mail = $ns.GetItemFromID($EntryId)
    "Sending from the mailbox $($mail.Parent.FolderPath) to $($mail.To): $($mail.Subject)"
    $mail.Send()
    'Sent.'
  }
}
