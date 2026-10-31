/**
 * GitHub Actions の sync ワークフローを GAS から定期起動する。
 * 起動先は「公開リポジトリ(このプログラム側)」。PAT も、そのリポジトリに対する Actions: Read and write。
 *
 * 準備:
 *  1. GitHub で Fine-grained personal access token を作成
 *     (対象リポジトリのみ / Repository permissions → Actions: Read and write)
 *  2. GAS の「プロジェクトの設定」→ スクリプト プロパティ に登録
 *       GITHUB_TOKEN  = 上のトークン
 *       GITHUB_OWNER  = GitHubのユーザー名
 *       GITHUB_REPO   = リポジトリ名
 *     (任意) GITHUB_REF = ブランチ名(既定 main) / WORKFLOW_FILE = ワークフロー名(既定 sync.yml)
 *  3. setupTrigger() を1回だけ手動実行 → 毎時トリガーが作られる
 *  4. 動作確認は triggerSync() を手動実行(GitHub の Actions 画面に run が増える)
 */
function triggerSync() {
  var p = PropertiesService.getScriptProperties();
  var owner = p.getProperty('GITHUB_OWNER');
  var repo = p.getProperty('GITHUB_REPO');
  var token = p.getProperty('GITHUB_TOKEN');
  var ref = p.getProperty('GITHUB_REF') || 'main';
  var wf = p.getProperty('WORKFLOW_FILE') || 'sync.yml';
  if (!owner || !repo || !token) {
    throw new Error('スクリプトプロパティ GITHUB_OWNER / GITHUB_REPO / GITHUB_TOKEN を設定してください');
  }
  var url = 'https://api.github.com/repos/' + owner + '/' + repo +
            '/actions/workflows/' + wf + '/dispatches';
  var options = {
    method: 'post',
    contentType: 'application/json',
    headers: {
      Authorization: 'Bearer ' + token,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28'
    },
    payload: JSON.stringify({ ref: ref, inputs: { dry_run: 'false' } }),
    muteHttpExceptions: true
  };
  var res, code;
  for (var i = 0; i < 3; i++) {            // 一時的な 5xx は最大3回まで再試行
    res = UrlFetchApp.fetch(url, options);
    code = res.getResponseCode();
    if (code === 204) return;              // 成功(本文なし)
    if (code < 500) break;
    Utilities.sleep(5000);
  }
  // 失敗時は例外 → GAS の「実行失敗」通知メールで気づける
  throw new Error('workflow_dispatch 失敗: HTTP ' + code + ' ' + res.getContentText());
}

/** 毎時トリガーを(再)作成する。1回だけ手動実行すればよい。 */
function setupTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'triggerSync') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('triggerSync').timeBased().everyHours(1).create();
}
