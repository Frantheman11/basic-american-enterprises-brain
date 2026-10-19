<#
  Outlook helper for the send-email skill. Uses the Outlook app on this computer.
  Nothing is sent unless -Action send is run with a draft's EntryId.

  -Action accounts                         list the mail accounts set up in Outlook
  -Action find   -Query "spencer"          look up a person: contacts, address book, recent sent mail
  -Action draft  -From <acct> -To <addr> -Subject <s> -BodyFile <path> [-Cc <addr>] [-Attach <paths>] [-EntryId <id>] [-Show] [-Discard]
                                           save (or update) a draft in that account's Drafts folder; prints its EntryId
                                           -Discard builds it without saving (test mode)
  -Action send   -EntryId <id>             send one saved draft
#>
param(
  [Parameter(Mandatory)][ValidateSet('accounts','find','draft','send')][string]$Action,
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
    } else {
      foreach ($p in 'From','To','Subject','BodyFile') { if (-not (Get-Variable $p -ValueOnly)) { throw "draft needs -$p" } }
      $acct = Get-Account $From
      $mail = $acct.DeliveryStore.GetDefaultFolder(16).Items.Add(0)
      # A plain assignment of a COM object property is silently ignored in PowerShell 5.1
      [void]$mail.GetType().InvokeMember('SendUsingAccount', [Reflection.BindingFlags]::SetProperty, $null, $mail, @($acct))
    }
    if ($To) { $mail.To = $To }
    if ($PSBoundParameters.ContainsKey('Cc')) { $mail.CC = $Cc }
    if ($Subject) { $mail.Subject = $Subject }
    if ($BodyFile) { $mail.Body = [IO.File]::ReadAllText((Resolve-Path $BodyFile), [Text.Encoding]::UTF8) }
    foreach ($f in $Attach) { if ($f) { [void]$mail.Attachments.Add((Resolve-Path $f).Path) } }
    "From: $($mail.SendUsingAccount.SmtpAddress)"
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

  'send' {
    if (-not $EntryId) { throw 'send needs -EntryId' }
    $mail = $ns.GetItemFromID($EntryId)
    "Sending from $($mail.SendUsingAccount.SmtpAddress) to $($mail.To): $($mail.Subject)"
    $mail.Send()
    'Sent.'
  }
}
