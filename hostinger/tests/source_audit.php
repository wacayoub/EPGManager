<?php
declare(strict_types=1);

function fetch_url(string $url, array $headers = [], int $timeout = 20): array {
    $ch = curl_init($url);
    $base = [
        'Accept: text/html,application/xhtml+xml,application/json,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language: ar,fr-FR;q=0.9,fr;q=0.8,en;q=0.6',
        'Cache-Control: no-cache',
        'Pragma: no-cache',
    ];
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_FOLLOWLOCATION => true,
        CURLOPT_MAXREDIRS => 5,
        CURLOPT_CONNECTTIMEOUT => 8,
        CURLOPT_TIMEOUT => $timeout,
        CURLOPT_ENCODING => '',
        CURLOPT_USERAGENT => 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36',
        CURLOPT_HTTPHEADER => array_merge($base, $headers),
        CURLOPT_SSL_VERIFYPEER => true,
        CURLOPT_SSL_VERIFYHOST => 2,
    ]);
    $body = curl_exec($ch);
    $err = $body === false ? curl_error($ch) : null;
    $status = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    $ctype = (string)curl_getinfo($ch, CURLINFO_CONTENT_TYPE);
    $final = (string)curl_getinfo($ch, CURLINFO_EFFECTIVE_URL);
    curl_close($ch);
    return [
        'url'=>$url,'final_url'=>$final,'status'=>$status,'content_type'=>$ctype,
        'bytes'=>is_string($body)?strlen($body):0,'error'=>$err,
        'sha256'=>is_string($body)?hash('sha256',$body):null,
        'sample'=>is_string($body)?preg_replace('/\s+/u',' ',substr(strip_tags($body),0,220)):'',
        'body'=>is_string($body)?$body:''
    ];
}

function summary(array $r, callable $check): array {
    $ok = $r['status'] >= 200 && $r['status'] < 300 && $r['bytes'] > 100 && $check($r['body']);
    unset($r['body']);
    $r['ok'] = $ok;
    return $r;
}

$today = (new DateTimeImmutable('now', new DateTimeZone('Africa/Casablanca')))->format('Y-m-d');

$tests = [];

$r=fetch_url('https://programmestv.sudinfo.be/programme-tv/chaine/2m-maroc/606',[
    'Referer: https://programmestv.sudinfo.be/',
    'Sec-Fetch-Dest: document','Sec-Fetch-Mode: navigate','Sec-Fetch-Site: same-origin','Upgrade-Insecure-Requests: 1'
]);
$tests['2m_sudinfo']=summary($r,fn($b)=>stripos($b,'2M Maroc')!==false && preg_match('/\b\d{1,2}:\d{2}\b/',$b));

$snrt=[
 'alaoula'=>'https://www.snrt.ma/ar/node/1208',
 'arryadia'=>'https://www.snrt.ma/ar/node/4070',
 'arrabiaa'=>'https://www.snrt.ma/ar/node/4071',
 'almaghribiya'=>'https://www.snrt.ma/ar/node/4072',
 'assadisa'=>'https://www.snrt.ma/ar/node/4073',
 'tamazight'=>'https://www.snrt.ma/ar/node/4075',
];
foreach($snrt as $k=>$u){
  $r=fetch_url($u);
  $tests['snrt_'.$k]=summary($r,fn($b)=>stripos($b,'grille')!==false || preg_match('/\b\d{1,2}H\d{2}\b/u',$b));
}

$r=fetch_url('https://www.medi1tv.com/ar/grille/arabic',['Referer: https://www.medi1tv.com/ar/']);
$tests['medi1_arabic']=summary($r,fn($b)=>preg_match('/\b\d{1,2}[:hH]\d{2}\b/u',$b));
$r=fetch_url('https://www.medi1tv.ma/ar/grille/maghreb',['Referer: https://www.medi1tv.ma/ar/']);
$tests['medi1_maghreb']=summary($r,fn($b)=>preg_match('/\b\d{1,2}[:hH]\d{2}\b/u',$b));

$bein='https://www.bein.com/ar/epg-ajax-template/?action=epg_fetch&category=sports&cdate='.$today.'&language=AR&loadindex=0&mins=00&offset=0&postid=25344&serviceidentity=bein.net';
$r=fetch_url($bein);
$tests['bein_direct']=summary($r,fn($b)=>stripos($b,'channels_')!==false && (stripos($b,'slider')!==false || stripos($b,'time')!==false));

$r=fetch_url('https://www.osn.com/apidata/channels?platform=Android');
$tests['osn_channels']=summary($r,fn($b)=>stripos($b,'encrypted')!==false || stripos($b,'guid')!==false);

foreach([
 'adsports1'=>'https://www.sport24.rest/channels/adsports/1',
 'dubaisports1'=>'https://www.sport24.rest/channels/dubaisports/1',
 'bein1'=>'https://www.sport24.rest/channels/bein/1'
] as $k=>$u){
  $r=fetch_url($u);
  $tests['sport24_'.$k]=summary($r,fn($b)=>stripos($b,'data-time')!==false || stripos($b,'schedule')!==false);
}

$r=fetch_url('https://elcinema.com/ar/tvguide/');
$tests['elcinema_index']=summary($r,fn($b)=>stripos($b,'tv-line')!==false || stripos($b,'tvguide')!==false);

$r=fetch_url('https://epgshare01.online/epgshare01/epg_ripper_AE1.xml.gz',[],30);
$tests['epgshare_ae1']=summary($r,fn($b)=>strlen($b)>1000);

$report=[
 'generated_at'=>(new DateTimeImmutable('now',new DateTimeZone('UTC')))->format(DATE_ATOM),
 'php'=>PHP_VERSION,
 'curl'=>curl_version()['version']??'',
 'openssl'=>OPENSSL_VERSION_TEXT,
 'tests'=>$tests,
 'pass'=>count(array_filter($tests,fn($x)=>$x['ok'])),
 'total'=>count($tests)
];
@mkdir(__DIR__.'/output',0775,true);
file_put_contents(__DIR__.'/output/source-audit.json',json_encode($report,JSON_PRETTY_PRINT|JSON_UNESCAPED_SLASHES|JSON_UNESCAPED_UNICODE));
foreach($tests as $name=>$x){
  printf("%-24s %s HTTP=%d bytes=%d %s\n",$name,$x['ok']?'PASS':'FAIL',$x['status'],$x['bytes'],$x['error']??'');
}
printf("TOTAL %d/%d PASS\n",$report['pass'],$report['total']);
exit($report['pass'] === $report['total'] ? 0 : 2);
