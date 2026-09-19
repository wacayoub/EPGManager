<?php
declare(strict_types=1);
date_default_timezone_set('Africa/Casablanca');

function get(string $url,array $headers=[],int $timeout=30): string {
  $ch=curl_init($url);
  curl_setopt_array($ch,[CURLOPT_RETURNTRANSFER=>true,CURLOPT_FOLLOWLOCATION=>true,CURLOPT_ENCODING=>'',CURLOPT_CONNECTTIMEOUT=>8,CURLOPT_TIMEOUT=>$timeout,
    CURLOPT_USERAGENT=>'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36',
    CURLOPT_HTTPHEADER=>array_merge(['Accept: text/html,application/xhtml+xml,application/json,application/xml;q=0.9,*/*;q=0.8','Accept-Language: ar-MA,ar;q=0.9,fr-FR;q=0.9,fr;q=0.8,en;q=0.6','Cache-Control: no-cache','Pragma: no-cache'],$headers)]);
  $b=curl_exec($ch);$c=(int)curl_getinfo($ch,CURLINFO_RESPONSE_CODE);$e=curl_error($ch);curl_close($ch);
  if(!is_string($b)||$c<200||$c>=300)throw new RuntimeException("HTTP $c $url $e"); return $b;
}
function clean($s){return trim(preg_replace('/\s+/u',' ',html_entity_decode((string)$s,ENT_QUOTES|ENT_HTML5,'UTF-8'))??'');}
function report($name,$ok,$data=[]){echo json_encode(['source'=>$name,'ok'=>$ok]+$data,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";}

$fail=0;

// 2M Sudinfo parser: locate exact HH:MM nodes and nearest ancestor with heading + duration.
try{
 $html=get('https://programmestv.sudinfo.be/programme-tv/chaine/2m-maroc/606',['Referer: https://programmestv.sudinfo.be/','Sec-Fetch-Site: same-origin']);
 $d=new DOMDocument();libxml_use_internal_errors(true);@$d->loadHTML('<?xml encoding="UTF-8">'.$html);$x=new DOMXPath($d);$rows=[];$seen=[];
 foreach($x->query('//*') as $node){$t=clean($node->textContent);if(!preg_match('/^(\d{1,2}:\d{2})$/',$t,$m))continue;$cur=$node;$block=null;
   for($i=0;$i<7&&$cur;$i++,$cur=$cur->parentNode){if(!($cur instanceof DOMElement))continue;$h=$x->query('.//h2|.//h3|.//h4|.//h5',$cur);if($h&&$h->length&&strlen(clean($cur->textContent))<1500){$block=$cur;break;}}
   if(!$block)continue;$h=$x->query('.//h2|.//h3|.//h4|.//h5',$block);$title=$h&&$h->length?clean($h->item(0)->textContent):'';if(!$title)continue;$k=$m[1].'|'.$title;if(isset($seen[$k]))continue;$seen[$k]=1;
   $txt=clean($block->textContent);preg_match('/\b(\d+h\s*\d*|\d+\s*min)\b/i',$txt,$du);$rows[]=[$m[1],$title,$du[1]??''];
 }
 $titles=array_column($rows,1);$must=['Qalb Aswad','Charqi ou Lgharbi','Info soir'];$missing=array_values(array_filter($must,fn($v)=>!in_array($v,$titles,true)));
 $ok=count($rows)>=20&&!$missing;report('2m_sudinfo_parser',$ok,['programmes'=>count($rows),'missing_expected'=>$missing,'sample'=>array_slice($rows,0,5)]);if(!$ok)$fail++;
}catch(Throwable $e){report('2m_sudinfo_parser',false,['error'=>$e->getMessage()]);$fail++;}

// SNRT parser
try{
 $urls=['AlAoula'=>'https://www.snrt.ma/ar/node/1208','Arryadia_HD'=>'https://www.snrt.ma/ar/node/4070','Arrabiaa'=>'https://www.snrt.ma/ar/node/4071','AlMaghribiya'=>'https://www.snrt.ma/ar/node/4072','Assadisa'=>'https://www.snrt.ma/ar/node/4073','Tamazight'=>'https://www.snrt.ma/ar/node/4075'];$counts=[];
 foreach($urls as $id=>$u){$h=get($u);$d=new DOMDocument();libxml_use_internal_errors(true);@$d->loadHTML('<?xml encoding="UTF-8">'.$h);$x=new DOMXPath($d);$n=0;
   foreach($x->query("//div[contains(concat(' ',normalize-space(@class),' '),' grille-line ')]") as $row){$tt=$x->query(".//div[contains(concat(' ',normalize-space(@class),' '),' grille-time ')]",$row);$pt=$x->query(".//*[contains(concat(' ',normalize-space(@class),' '),' program-title-sm ')]",$row);if($tt&&$tt->length&&$pt&&$pt->length)$n++;}
   $counts[$id]=$n;
 }
 $ok=array_sum($counts)>=20&&count(array_filter($counts))>=4;report('snrt_parser',$ok,['counts'=>$counts,'programmes'=>array_sum($counts)]);if(!$ok)$fail++;
}catch(Throwable $e){report('snrt_parser',false,['error'=>$e->getMessage()]);$fail++;}

// beIN direct parser
try{
 $date=(new DateTimeImmutable('now',new DateTimeZone('Asia/Qatar')))->format('Y-m-d');$u='https://www.bein.com/ar/epg-ajax-template/?'.http_build_query(['action'=>'epg_fetch','category'=>'sports','cdate'=>$date,'language'=>'AR','loadindex'=>0,'mins'=>'00','offset'=>0,'postid'=>'25344','serviceidentity'=>'bein.net']);
 $h=get($u);$d=new DOMDocument();libxml_use_internal_errors(true);@$d->loadHTML('<?xml encoding="UTF-8">'.$h);$x=new DOMXPath($d);$channels=0;$programmes=0;$bad=0;
 foreach($x->query("//div[starts-with(@id,'channels_')]") as $b){$channels++;foreach($x->query(".//*[contains(concat(' ',normalize-space(@class),' '),' slider ')]/ul[1]/li",$b) as $li){$title=$x->query(".//*[contains(concat(' ',normalize-space(@class),' '),' title ')]",$li);$time=$x->query(".//*[contains(concat(' ',normalize-space(@class),' '),' time ')]",$li);if($title&&$title->length&&$time&&$time->length&&preg_match('/\d{2}:\d{2}/',clean($time->item(0)->textContent)))$programmes++;else$bad++;}}
 $ok=$channels>=10&&$programmes>=20;report('bein_parser',$ok,['channels'=>$channels,'programmes'=>$programmes,'invalid_items'=>$bad]);if(!$ok)$fail++;
}catch(Throwable $e){report('bein_parser',false,['error'=>$e->getMessage()]);$fail++;}

// Sport24 exact UTC data-time parser
try{
 $targets=['adsports1'=>'https://www.sport24.rest/channels/adsports/1','dubaisports1'=>'https://www.sport24.rest/channels/dubaisports/1','bein1'=>'https://www.sport24.rest/channels/bein/1'];$counts=[];
 foreach($targets as $k=>$u){$h=get($u);$d=new DOMDocument();libxml_use_internal_errors(true);@$d->loadHTML('<?xml encoding="UTF-8">'.$h);$x=new DOMXPath($d);$n=0;
  foreach($x->query("//*[@data-time]") as $node){$v=$node->getAttribute('data-time');if(preg_match('/^20\d\d-\d\d-\d\dT/',$v))$n++;}$counts[$k]=$n;}
 $ok=min($counts)>1;report('sport24_parser',$ok,['data_time_nodes'=>$counts]);if(!$ok)$fail++;
}catch(Throwable $e){report('sport24_parser',false,['error'=>$e->getMessage()]);$fail++;}

// OSN encryption/decryption + timeline
try{
 $key=hex2bin('65a04b9b5591f27c837fac433274b494403e0eec5b43698060c28e1162d4460f');$iv=hex2bin('b7cc0a48d6d023bc1a2a670953ec5622');
 $dec=function($body)use($key,$iv){$j=json_decode($body,true);if(isset($j['encrypted'])){$raw=openssl_decrypt(base64_decode($j['encrypted'],true),'aes-256-cbc',$key,OPENSSL_RAW_DATA,$iv);return json_decode($raw,true);}return $j;};
 $enc=function($data)use($key,$iv){return base64_encode(openssl_encrypt(json_encode($data,JSON_UNESCAPED_SLASHES),'aes-256-cbc',$key,OPENSSL_RAW_DATA,$iv));};
 $chs=$dec(get('https://www.osn.com/apidata/channels?platform=Android'));if(!is_array($chs)||count($chs)<5)throw new RuntimeException('bad channel decrypt');
 $guids=array_values(array_filter(array_map(fn($x)=>$x['guid']??null,array_slice($chs,0,5))));$start=(new DateTimeImmutable('today'))->getTimestamp()*1000;$end=(new DateTimeImmutable('tomorrow'))->getTimestamp()*1000;$data=['channelGuid'=>implode('|',$guids),'startTime'=>$start,'endTime'=>$end];
 $u="https://www.osn.com/apidata/tv-schedule-timeline?t=batch1-time{$start}-{$end}-boxAndroid";$payload=$dec(get($u,['X-Encrypted-Data: '.$enc($data)]));$listings=0;foreach(($payload['entries']??[]) as $e)$listings+=count($e['listings']??[]);
 $ok=count($chs)>=50&&$listings>0;report('osn_parser',$ok,['channels'=>count($chs),'sample_batch_listings'=>$listings]);if(!$ok)$fail++;
}catch(Throwable $e){report('osn_parser',false,['error'=>$e->getMessage()]);$fail++;}

// ElCinema index + one real channel page
try{
 $h=get('https://elcinema.com/ar/tvguide/');$d=new DOMDocument();libxml_use_internal_errors(true);@$d->loadHTML('<?xml encoding="UTF-8">'.$h);$x=new DOMXPath($d);$ids=[];
 foreach($x->query("//*[contains(concat(' ',normalize-space(@class),' '),' tv-line ')]//a[@href]") as $a){if(preg_match('~/tvguide/(\d+)/?$~',$a->getAttribute('href'),$m))$ids[$m[1]]=clean($a->textContent);}
 $sample=0;$sampleId=array_key_first($ids);if($sampleId){$p=get("https://elcinema.com/ar/tvguide/$sampleId/");$dd=new DOMDocument();@$dd->loadHTML('<?xml encoding="UTF-8">'.$p);$xx=new DOMXPath($dd);$sample=$xx->query("//*[contains(concat(' ',normalize-space(@class),' '),' padded-half ')]")->length;}
 $ok=count($ids)>=50&&$sample>0;report('elcinema_parser',$ok,['channels_discovered'=>count($ids),'sample_channel'=>$sampleId,'sample_cards'=>$sample]);if(!$ok)$fail++;
}catch(Throwable $e){report('elcinema_parser',false,['error'=>$e->getMessage()]);$fail++;}

// OpenEPG + EPGShare XML/GZIP validation
foreach(['openepg'=>'https://www.open-epg.com/files/egypt1.xml.gz','epgshare'=>'https://epgshare01.online/epgshare01/epg_ripper_AE1.xml.gz'] as $name=>$u){
 try{$raw=get($u,[],45);$xml=gzdecode($raw);if($xml===false)throw new RuntimeException('gzip decode');$sx=simplexml_load_string($xml);if(!$sx)throw new RuntimeException('xml parse');$ch=count($sx->channel);$pr=count($sx->programme);$placeholder=0;foreach($sx->programme as $p){if(preg_match('/^(tv guide is not available|no information|n\/a)$/i',clean((string)$p->title[0])))$placeholder++;}
  $ok=$ch>1&&$pr>20;report($name.'_xmltv',$ok,['channels'=>$ch,'programmes'=>$pr,'placeholder_programmes'=>$placeholder]);if(!$ok)$fail++;
 }catch(Throwable $e){report($name.'_xmltv',false,['error'=>$e->getMessage()]);$fail++;}
}
exit($fail?2:0);
