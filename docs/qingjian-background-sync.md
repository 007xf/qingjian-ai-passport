<p align="right">
  <a href="qingjian-background-sync.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Background sync and growth accounting

Qingjian can keep reading usage and synchronizing the selected badge after its
editor window closes or the editor quits. This runs on the signed-in Mac user
session. The Mac must remain powered on and able to run; an iPhone cannot replace
it as the sync host in this release.

## Enable background sync

1. Put Qingjian in a stable location, preferably Applications, and open it.
2. In connection settings, choose USB or select the intended Bluetooth badge.
   Complete the initial Bluetooth pairing and any macOS Bluetooth permission
   prompt before relying on automatic reconnect.
3. Open Background sync and enable it. Check that the panel reports a running
   service and the expected device connection.
4. Close or quit the editor. The enabled background service continues to run
   and is registered to start when this Mac user signs in.

Background work incrementally refreshes local usage sources approximately every 60 seconds (or every hour when selected)
and checks a connected badge's status approximately every 30 seconds. Network or
device response time can delay a cycle. The editor displays the service's latest
result rather than opening a competing device connection.

Disable Background sync in Qingjian to stop this service. Reopen the editor after
moving or updating the app and check its background status; if its saved runtime
location is no longer available, enable it again from the new app location.

## Bluetooth reconnect and uploads

Automatic Bluetooth connection uses the badge already selected in connection
settings. It does not silently choose a nearby device or replace the selected
Bluetooth connection with USB. A new Mac still needs its own device selection,
pairing and permissions.

If the selected badge switches off, leaves range or disconnects, the service
closes the failed connection and retries with increasing delays, up to 60 seconds.
A permission problem uses a longer delay while waiting for the user to resolve
it. After system wake, the service schedules a new status and usage refresh.
Successful reconnection resumes ordinary sync.

Only one background owner operates the transport. Editor commands are processed
in order through it. An upload whose confirmation was lost is not automatically
sent again: inspect the result in Qingjian and explicitly retry the upload if
needed. Routine background refresh does not replay profile edits or avatar
uploads.

Custom avatar uploads and restoring a default avatar require USB. Bluetooth can
continue to synchronize text, settings, time, usage and quota. The Mac retains
the three selected stage images; the device caches one custom stage image, so a
different custom image can remain pending until the next USB update.

## What contributes to growth

The growth value is:

```text
Codex Tokens in its current weekly/reset-card cycle
  + Cursor Tokens in its current account billing month
```

Each source keeps its own cycle. Cursor's billing month does not necessarily
begin on the first day of the calendar month. Recording a used Codex reset card
starts a new Codex counter period without clearing Cursor's month. That action
does not redeem or consume a real reset credit.

Codex uses the Token counters in this Mac's readable session and archived-session
logs. Its count includes input and output; cached input is already included and
is not added a second time. It is a local-log count, not a guaranteed total across
every Mac using the account. Historical logs do not prove the account identity
of every individual request.

Cursor uses the signed-in account's actual usage counters for the billing-month
start through the observation time. The whole-interval aggregate includes four
separate components: uncached input, output, cache write and cache read. Their
sum is checked against the per-group totals. This aggregate has no pagination;
the collector does not mistake the first page of individual requests for a full
month. Group totals are used for validation, not added again to the final count.

Repeated cached context can contribute many Tokens over multiple model calls.
The growth value is not a bill, money spent, remaining allowance or a conversion
from a quota percentage. Cursor model usage and other-model usage percentages
remain separate readouts. The Cursor integration uses its client's internal
read-only usage interface; an upstream change may require an updated Qingjian.

Existing names, images and evolution thresholds remain user settings. A change
in either confirmed source period starts a new growth-cycle marker. Adding
Cursor information to an older marker does not by itself fabricate a reset.
An actual period reset can reduce the sum and therefore change the avatar stage.

## Last readings and unavailable sources

The editor retains previous valid readings with their original observation time
and marks them as last readings when they are stale. An unavailable battery
sample, temporary network failure or incomplete log scan does not become a zero.
If no prior valid reading exists, the corresponding value stays unknown.

Codex source observations and Cursor source observations have separate clocks
and expiry times. Cursor usage can use its account-bound cache for up to five
minutes; repeatedly displaying or sending it does not renew its source time.
Changing the current account prevents reuse of the previous account's cache.

When both Token sources are fresh and complete, Qingjian sends their sum to the
badge. If a source is missing or stale, the editor can show the last known source
counts and their combined last reading, but does not present that sum as a new
complete observation or advance the stage. The badge keeps its preceding Token
value without having its observation time refreshed. Different selected badges
do not inherit one another's cached battery status.

## Display off is not system sleep

The optional continue-while-display-is-off setting allows the display to turn
off while preventing automatic idle system sleep for the lifetime of the
background service. This can increase power use. Disabling background sync
releases that keep-awake request. A disconnected badge also releases it after a 90-second grace period; reconnecting restores it. Manual Sync refreshes immediately, while UI status polls stay silent.

It does not guarantee operation during manual Sleep, closed-lid system sleep,
shutdown, logout or a macOS power-management suspension. The service cannot
collect or transmit continuously while the Mac itself is asleep; it resumes
attempts after the Mac wakes. Turning off the badge display with triple OK is
also separate from putting the Mac to sleep.

## Scope and verification

This release provides a Mac sync host, USB/Bluetooth badge transport and a Mac
editor. It does not provide an iPhone application or an iPhone background relay.
No voice or badge desktop-pet function is introduced by background sync.

The accounting tests cover source totals, caching, stale observations, cycle
changes and preservation of existing thresholds. Those tests do not establish
physical Bluetooth range, sleep/wake reliability or new-device acceptance.
Consult the release notes for the exact app/firmware build and its separately
reported hardware checks.
