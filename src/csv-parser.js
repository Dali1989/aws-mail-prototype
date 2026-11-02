(function(root){
  'use strict';
  function parseCsv(text,allowed,limit=5){
    if(typeof text!=='string')throw new Error('CSVを読み込めません。');
    text=text.replace(/^\uFEFF/,'');
    const records=[];let row=[],field='',quoted=false,closed=false,atStart=true;
    function finishField(){row.push(field);field='';closed=false;atStart=true;}
    function finishRow(){finishField();if(!(row.length===1&&row[0]===''))records.push(row);row=[];}
    for(let i=0;i<text.length;i++){
      const c=text[i];
      if(quoted){if(c==='"'){if(text[i+1]==='"'){field+='"';i++;}else{quoted=false;closed=true;}}else field+=c;continue;}
      if(c===','){finishField();continue;}
      if(c==='\r'||c==='\n'){if(c==='\r'&&text[i+1]==='\n')i++;finishRow();continue;}
      if(closed)throw new Error('CSVの閉じ引用符の後には区切りまたは改行が必要です。');
      if(c==='"'){if(!atStart)throw new Error('CSVの引用符の位置を確認してください。');quoted=true;atStart=false;continue;}
      field+=c;atStart=false;
    }
    if(quoted)throw new Error('CSVの引用符が閉じていません。');
    if(row.length||field||closed)finishRow();
    if(!records.length||JSON.stringify(records[0])!==JSON.stringify(['companyName','contactName','email']))throw new Error('列は companyName,contactName,email の順にしてください。');
    const entries=records.slice(1);if(!entries.length||entries.length>limit)throw new Error(`CSVは1～${limit}件にしてください。`);
    const seen=new Set();return entries.map((cols,i)=>{
      if(cols.length!==3)throw new Error(`${i+2}行目の列数を確認してください。`);
      const [companyName,contactName,email]=cols.map(s=>s.trim());
      if([companyName,contactName].some(s=>!s||s.length>100||/[\r\n]/.test(s)))throw new Error(`${i+2}行目の会社名・担当者名を確認してください。`);
      if(!/^[^\s@,]+@[^\s@,]+\.[^\s@,]+$/.test(email)||!allowed.includes(email))throw new Error(`${i+2}行目のメールをテスト宛先の許可リストと照合してください。`);
      const key=email.toLowerCase();if(seen.has(key))throw new Error('同じメールアドレスが重複しています。');seen.add(key);
      return {companyName,contactName,email};
    });
  }
  root.CsvInput={parseCsv};
  if(typeof module!=='undefined'&&module.exports)module.exports={parseCsv};
})(globalThis);
