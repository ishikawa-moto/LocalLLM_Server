'use strict';
let ocrEnabled=false, ocrPending=false, ocrRequest=null, ocrPolling=false, ocrUploading=false, analysisJob=null;
const ocrLabels={queued:'待機中',running:'読み取り中',paused:'一時待機中（自動再開）',cancelling:'停止処理中',expiring:'期限切れ処理中',complete:'読み取り完了',failed:'読み取り失敗',cancelled:'取消済み',expired:'期限切れ'};
function ocrNotice(text){$('ocr-notice').textContent=text;}
function updateOcrControls(){
  $('ocr-submit').disabled=busy||ocrPending||ocrUploading;
  $('send').disabled=busy||ocrPending;
  $('delete').disabled=busy||ocrPending||!conversation;
}
async function downloadOcr(job,artifact){
  const r=await fetch('/api/ocr/artifact',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Chat-Request':'1'},body:JSON.stringify({conversation_id:job.conversation_id,job_id:job.job_id,format:artifact.format})});
  if(!r.ok)throw Error('結果を取得できません。期限と接続を確認してください。');
  const blob=await r.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');
  a.href=url;a.download=artifact.filename;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function renderOcr(jobs){
  $('ocr-results').replaceChildren();
  for(const job of jobs){
    const card=document.createElement('article');card.className='ocr-result';
    const label=document.createElement('strong');
    label.textContent=(ocrLabels[job.state]||'状態を確認中')+` · ${job.completed_pages} / ${job.page_count||'?'} ページ`;
    card.append(label);
    if(job.cancel_requested&&!job.worker_quiescent){const note=document.createElement('p');note.textContent='結果の公開を停止しました。処理側の停止確認を待っています。';card.append(note);}
    if(job.wait_reason||job.error_code){const note=document.createElement('p');note.textContent=job.wait_reason||job.error_code;card.append(note);}
    if(job.text){const preview=document.createElement('details'),summary=document.createElement('summary'),text=document.createElement('pre');summary.textContent='読み取った文字を確認';text.textContent=job.text;preview.append(summary,text);card.append(preview);}
    if(job.preview_truncated){const note=document.createElement('p');note.textContent='表示は一部です。全体はMarkdownをダウンロードしてください。';card.append(note);}
    if(job.state==='complete'){
      for(const artifact of job.artifacts){const button=document.createElement('button');button.type='button';button.textContent=artifact.format.toUpperCase()+'を保存';button.onclick=()=>downloadOcr(job,artifact).catch(e=>ocrNotice(e.message));card.append(button);}
      const analyze=document.createElement('button');analyze.type='button';analyze.textContent='Qwenに分析を依頼';analyze.disabled=busy||ocrPending;
      analyze.onclick=()=>{analysisJob=job.job_id;$('brain').checked=false;$('brain').onchange();$('prompt').value='この文書の内容を要約し、確認が必要な点を説明してください。';$('prompt').focus();ocrNotice('入力欄で依頼内容を確認し、「送信」を押すとローカルQwenで分析します。');};card.append(analyze);
    }else if(!['failed','cancelled','expired'].includes(job.state)||!job.worker_quiescent){
      const cancel=document.createElement('button');cancel.type='button';cancel.textContent='読み取りを取り消す';cancel.disabled=job.cancel_requested;
      cancel.onclick=async()=>{try{await api('/api/ocr/cancel',{conversation_id:job.conversation_id,job_id:job.job_id});await refreshOcr();}catch(e){ocrNotice(e.message);}};card.append(cancel);
    }
    $('ocr-results').append(card);
  }
}
async function refreshOcr(){
  if(!ocrEnabled||!conversation||ocrPolling)return;
  const cid=conversation;ocrPolling=true;
  try{const data=await api('/api/ocr/status',{conversation_id:cid});if(conversation!==cid)return;
    ocrPending=data.jobs.some(j=>!['complete','failed','cancelled','expired'].includes(j.state)||!j.worker_quiescent);
    renderOcr(data.jobs);updateOcrControls();
  }catch(e){if(conversation===cid)ocrNotice('読み取り状態を取得できません。接続回復後に再確認します。');}
  finally{ocrPolling=false;}
}
function encodeOcr(buffer){let raw='';const bytes=new Uint8Array(buffer);for(let i=0;i<bytes.length;i+=8192)raw+=String.fromCharCode(...bytes.subarray(i,i+8192));return btoa(raw);}
$('ocr-file').onchange=()=>{ocrRequest=null;analysisJob=null;};
$('ocr-submit').onclick=async()=>{
  if(busy||ocrPending||ocrUploading)return;
  ocrUploading=true;updateOcrControls();analysisJob=null;
  try{
    if(!ocrRequest){const file=$('ocr-file').files[0];if(!file)throw Error('画像またはPDFを選んでください。');
      const ext='.'+file.name.split('.').pop().toLowerCase(),extension=ext==='.jpeg'?'.jpg':ext;
      if(!['.png','.jpg','.webp','.pdf'].includes(extension)||file.size<1||file.size>4*1024*1024)throw Error('PNG・JPEG・WebP・PDFの4MiB以下のファイルを選んでください。');
      if(!conversation)await newChat();
      // The stable request survives an uncertain response in this page; no private bytes in Web Storage.
      const bytes=new Uint8Array(16);crypto.getRandomValues(bytes);
      ocrRequest={job_id:Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join(''),conversation_id:conversation,extension,formats:['md',...($('ocr-html').checked?['html']:[]),...($('ocr-csv').checked?['csv']:[])],bytes:encodeOcr(await file.arrayBuffer())};
    }
    const cid=ocrRequest.conversation_id;if(cid!==conversation)throw Error('元のChatを開いてから再試行してください。');
    $('ocr-submit').disabled=true;ocrNotice('アップロード中…');
    await api('/api/ocr/submit',ocrRequest);ocrRequest=null;$('ocr-file').value='';ocrNotice('受け付けました。完了後にファイルを保存できます。');await refreshOcr();
  }catch(e){ocrNotice(e.message+' 応答が不明な場合、再試行では同じ依頼IDを使います。');}
  finally{ocrUploading=false;updateOcrControls();}
};
(async()=>{try{const cfg=await api('/api/ocr/config');ocrEnabled=cfg.enabled===true;$('ocr-tools').hidden=!ocrEnabled;await refreshOcr();}catch{}})();
setInterval(()=>{if(!document.hidden)refreshOcr();},3000);
