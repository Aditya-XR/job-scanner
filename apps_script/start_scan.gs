// Starts the daily scan on GitHub every morning at about 07:20 IST.
//
// GitHub's own schedule has been starting runs 3-10 hours late, but a run started through the
// API (workflow_dispatch) begins at once. The workflow's schedule stays as a backup: it only
// scans if no scan has succeeded yet that day.
//
// Lives in the Job Scanner sheet (Extensions > Apps Script) and runs as the sheet's owner.
// Setup, once:
//   1. Project Settings > Script Properties: GITHUB_TOKEN = a fine-grained GitHub token for
//      Aditya-XR/job-scanner only, with the single permission "Actions: Read and write".
//   2. Run install() and allow access. It checks the token and creates the daily trigger.

const REPO = 'Aditya-XR/job-scanner';
const WORKFLOW = 'daily.yml';
const API = 'https://api.github.com/repos/' + REPO + '/actions/workflows/' + WORKFLOW;

function install() {
  const res = github_('get', '');
  if (res.getResponseCode() !== 200) {
    throw new Error('GitHub refused the token (HTTP ' + res.getResponseCode() + '): ' + res.getContentText());
  }
  ScriptApp.getProjectTriggers()
    .filter(function (t) { return t.getHandlerFunction() === 'startScan'; })
    .forEach(function (t) { ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('startScan')
    .timeBased().everyDays(1).atHour(7).nearMinute(20).inTimezone('Asia/Kolkata')
    .create();
  Logger.log('Token works. startScan() will run every day at about 07:20 IST.');
}

function startScan() {
  for (let attempt = 1; ; attempt++) {
    const res = github_('post', '/dispatches', { ref: 'main' });
    const code = res.getResponseCode();
    if (code === 204 || code === 200) return;
    // A server error is usually gone a minute later; anything else (expired token...) won't be.
    if (code < 500 || attempt === 3) {
      throw new Error('Could not start the scan (HTTP ' + code + '): ' + res.getContentText());
    }
    Utilities.sleep(60 * 1000);
  }
}

function github_(method, path, body) {
  const token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  if (!token) throw new Error('Script property GITHUB_TOKEN is not set (Project Settings > Script Properties)');
  const options = {
    method: method,
    headers: {
      Authorization: 'Bearer ' + token,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
    },
    muteHttpExceptions: true,
  };
  if (body) {
    options.contentType = 'application/json';
    options.payload = JSON.stringify(body);
  }
  return UrlFetchApp.fetch(API + path, options);
}
