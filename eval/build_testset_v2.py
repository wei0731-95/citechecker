"""Builds eval/testset_v2.jsonl (SPEC_V2 section 5, Phase T). 94 items, AI-annotated.

Briefs are written by Claude after reading the full text of the cited judgments in the database.
Gold labels were fixed before any system run. This script validates every fact it can (T4) and
only then writes the file. Second annotation (T5) is merged by eval/apply_second_annotation.py.
Run: python eval/build_testset_v2.py
"""
import glob
import json
import re
import sqlite3
from pathlib import Path

from citecheck.statute_check import parse_article

DB = Path("data/citecheck.db")
OUT = Path("eval/testset_v2.jsonl")

# ---------------------------------------------------------------- source judgments (T2)
J = {
    114: (115, "TPSV,115,台上,114,20260304,1"), 2179: (114, "TPSV,114,台上,2179,20260513,1"),
    339: (115, "TPSV,115,台上,339,20260415,1"), 566: (115, "TPSV,115,台上,566,20260730,1"),
    1966: (114, "TPSV,114,台上,1966,20260204,1"), 891: (115, "TPSV,115,台上,891,20260805,1"),
    2102: (114, "TPSV,114,台上,2102,20260205,1"), 19: (115, "TPSV,115,台上,19,20260429,1"),
    552: (115, "TPSV,115,台上,552,20260429,1"), 362: (112, "TPSV,112,台上,362,20230510,1"),
    1880: (111, "TPSV,111,台上,1880,20230510,1"), 2014: (114, "TPSV,114,台上,2014,20260401,1"),
    367: (112, "TPSV,112,台上,367,20230510,1"), 1715: (114, "TPSV,114,台上,1715,20260514,1"),
    1042: (115, "TPSV,115,台上,1042,20260709,1"), 843: (112, "TPSV,112,台上,843,20230531,1"),
    1530: (114, "TPSV,114,台上,1530,20260709,1"), 670: (115, "TPSV,115,台上,670,20260527,1"),
}

ITEMS: list[dict] = []


def jcite(no):
    return f"最高法院{J[no][0]}年度台上字第{no}號判決"


def add(cat, brief, citations, **extra):
    ITEMS.append({"category": cat, "brief_text": brief, "citations": citations, **extra})


def jc(raw, status, jid, l2, passage, note, **kw):
    return {"type": "JUDGMENT", "raw": raw, "gold_status": status, "gold_jid": jid, "gold_l2": l2,
            "gold_passage": passage, "note": note, **kw}


def sc(raw, status, law, art, note="", **kw):
    return {"type": "STATUTE", "raw": raw, "gold_status": status, "gold_law_name": law,
            "gold_article": art, "note": note, **kw}


def content(cat, no, claim, l2, passage, note):
    raw = jcite(no)
    add(cat, f"{claim}，{raw}意旨參照。", [jc(raw, "FOUND", J[no][1], l2, passage, note)])


# ---------------------------------------------------------------- A: the Court's own holding (12)
A = "COURT_HOLDING"
content("A", 114, "按植栽屬於土地的出產物，在尚未與土地分離前是土地的一部分，不能脫離土地單獨作為事實上處分權移轉的客體，而水池、擋土圍牆則要看附著情形與交易觀念", A,
        "又不動產之出產物，尚未分離者，為該不動產之部分(民法第66條第2項參照)…均非得脫離該等土地，單獨就屬其成分之出產物、定著物為事實上處分權之移轉", "本院判斷四（seg 9）")
P339 = "判斷婚姻關係准否解消，應以該婚姻是否已生破綻，夫妻雙方有無繼續共同生活之可能性為標準"
content("A", 339, "按審酌婚姻能否解消，重點在婚姻是否已經出現破綻、夫妻還有沒有繼續共同生活的可能，而不是單看哪一方有過失", A, P339, "本院判斷四（seg 5）")
content("A", 1966, "按稅捐機關的債權屬於公法上權利，在稅法沒有明文準用之前，不能直接適用或類推適用民法第242條債權人代位權的規定，向債務人以外的第三人行使代位權", A,
        "於相關租稅法規未設有準用民法第242條規定或其他相類規定之明文前，自不得逕行適用或類推適用民法第242條規定而直接對納稅義務人以外之第三人行使代位權", "本院廢棄發回之理由㈠（seg 11）")
P891 = "各繼承人對遺產並無應有部分，亦不得僅依其應繼分之比例行使權利"
content("A", 891, "按繼承人有數人時，遺產分割前為全體公同共有，遺產所生的租金收益也屬遺產，請求給付須由公同共有人全體為之，不能只依自己的應繼分比例單獨請求", A,
        P891 + "…應由公同共有人全體或得公同共有人全體同意，不得依應繼分比例單獨行使權利", "本院之判斷㈠㈡（seg 9、10）")
P2102 = "按買受人主張買賣標的物有瑕疵，出賣人應負瑕疵擔保或不完全給付之損害賠償責任者，應由買受人就瑕疵之事實負舉證責任"
content("A", 2102, "按買受人主張買賣標的物有瑕疵，要求出賣人負責時，瑕疵確實存在這件事要由買受人自己舉證", A, P2102, "本院判斷四（seg 7）")
content("A", 19, "按以不實文件或利用第三人的疏失，讓已表明交易條件的相對人誤以為自己符合條件而成交，也屬於民法上的詐欺行為", A,
        "倘表意人明知自己不符合該條件或限制，而以不實之文件或利用第三人之疏失或錯誤，使相對人誤以為表意人符合其所設條件或限制，或加深、保持該錯誤之認知，而與之為交易，亦難謂非欺罔行為",
        "本院五（seg 6）")
P552 = "前者以共同侵權人間有意思聯絡為必要，各自分擔實行行為一部，而互相利用他人之行為；後者須各行為人之行為均為其所生損害之共同原因"
content("A", 552, "按共同侵權行為分成兩種：一種要求行為人之間有意思聯絡，另一種只要各人的行為都是造成同一損害的共同原因即可", A, P552, "本院四（seg 5）")
content("A", 362, "按主張雙方成立消費借貸的人，必須證明借貸意思表示合致及款項已交付，只證明有匯款而證明不了借貸合意，不能認定有借貸關係", A,
        "倘當事人主張與他方有消費借貸關係存在者，自應就該借貸意思互相表示合致及借款業已交付之事實，均負舉證之責任，其僅證明有金錢之交付，未能證明借貸意思表示合致者，仍不能認為有該借貸關係存在",
        "本院判斷（seg 5）")
P1880 = "當事人之一方因可歸責於他方之事由，致不能給付者，得請求對待給付。但其因免給付義務所得之利益或應得之利益，均應由其所得請求之對待給付中扣除之"
content("A", 1880, "按債務人的給付不能如果可歸責於債權人，債務人仍可請求對待給付，只是要扣除因免除給付義務所得到的利益", A, P1880, "本院五（seg 6）")
content("A", 367, "按遺產分割前，繼承人對遺產全部公同共有，應繼分只是對全部遺產權利義務的繼承比例，並不是對個別遺產的權利比例", A,
        "又應繼分係各繼承人對於遺產之一切權利義務，所得繼承之比例，並非對於個別遺產之權利比例", "本院五㈠（seg 8）")
content("A", 1715, "按區分所有人使用基地超過自己應有部分的部分沒有法律上原因，須對基地應有部分超過的其他區分所有人返還不當得利，有數人時依超過比例請求", A,
        "該區分所有權人就其使用基地超過部分之利益，即欠缺法律上原因，對於基地應有部分超過者之他區分所有權人應負給付不當得利之責", "本院之判斷四（seg 10）")
content("A", 1042, "按共有物的分管約定不一定要有書面，只要共有人實際劃定使用範圍、彼此容忍多年，就可認為有默示分管契約，受讓人知情或可得而知時也受拘束", A,
        "共有物分管之約定，不以訂立書面為要件，倘共有人間實際上劃定使用範圍，對各自占有管領之部分，互相容忍，對於他共有人使用、收益各自占有之部分，未予干涉，已歷有年所，即非不得認有默示分管契約之存在",
        "本院廢棄發回之理由㈠（seg 6）")

# ---------------------------------------------------------------- B: party argument, not rebutted (6)
B = "PARTY_ARGUMENT"
content("B", 2179, "按被告未出席說明會，是因為原告臨時倉促通知、來不及更動行程，並非可歸責於被告，不構成違約", B,
        "至伊未出席系爭說明會，係因上訴人倉促通知，伊不及更動行程所致，非可歸責於伊等語", "被上訴人抗辯（seg 3）；本院未就此表示意見")
content("B", 566, "按合資興建的建案尚未結案前，合資人不能請求清算，須待建案清算了結後才能與對方進行結算", B,
        "縱認有類似合夥關係，然系爭建案尚未清算了結，自無法與上訴人進行清算", "被上訴人抗辯（seg 3）；本院未就此表示意見")
content("B", 19, "按受詐欺而為意思表示的人，若遲至十年之後才行使撤銷權，撤銷權已因除斥期間經過而消滅", B,
        "上訴人遲至103年7月11日始發函撤銷買賣之意思表示，亦已罹於10年除斥期間", "被上訴人抗辯（seg 4）；本院未就此表示意見")
content("B", 1042, "按依興建當時有效的建築技術規則，公寓可免設屋頂避難平臺，因此屋頂平臺並不是供避難使用的空間", B,
        "依系爭公寓申請建照當時有效之建築技術規則，系爭公寓免設屋頂避難平臺，直通樓梯亦得免通達屋頂平臺", "上訴人於事實審之主張（seg 7 引述）；本院僅指摘原審未記載取捨意見")
content("B", 552, "按侵權行為損害賠償請求權，自被害人知有損害及賠償義務人時起兩年間不行使就會消滅，被害人起訴時已逾兩年的，義務人得拒絕給付", B,
        "其提起本件訴訟已逾2年消滅時效", "上訴人高慧萍抗辯（seg 3）；本院未就此表示意見")
content("B", 362, "按一方把款項匯給另一方，如果是出於贈與而非借貸，就不成立消費借貸，也不能要求受款人返還", B,
        "被上訴人即稱願贈與購屋所需款項及代償系爭○○房地之貸款，伊並未向被上訴人借款等語", "上訴人抗辯（seg 3）；本院僅稱「是否全然無稽」，未表示結論")

# ---------------------------------------------------------------- C: lower-court view, not rebutted (6)
C = "LOWER_COURT_VIEW"
content("C", 843, "按侵權行為損害賠償請求權已因時效完成而消滅時，義務人得拒絕給付，法院可據此駁回請求", C,
        "另侵權行為損害賠償責任，其請求權已罹於時效，被上訴人拒絕給付，自屬可採", "原審見解（seg 12）；本院未就時效表示意見")
content("C", 670, "按主張合建契約的人若無法證明簽名及印文真正，而他方否認，該契約即難認為真正，依該契約所為的解除契約意思表示就不生效力", C,
        "上訴人所提3人合建契約，既經陳勝男否認其上簽名及印文之真正，上訴人就此未舉證證明，難認為真正，其依3人合建契約對陳勝男為解除契約之意思表示，不生效力",
        "原審見解（seg 6）；本院未就此表示意見")
content("C", 367, "按子女為母親支付醫療費用，既然子女對父母本有扶養義務，即使是出於孝敬而支付，也屬履行義務，不成立無因管理", C,
        "傅惠係傅鄭淑婉之女，其給付非無義務，縱令傅鄭淑婉非不能維持生活，乃基於孝敬母親而支付上開費用，即非無義務，尚不成立無因管理", "原審見解（seg 7）；本院廢棄部分與此無涉")
content("C", 114, "按無權占用他人山坡地、主要作植栽使用的，不當得利的數額可以土地申報地價年息百分之三計算", C,
        "上訴人占用系爭土地所受利益，應以土地申報地價年息3%計算為適當", "原審見解（seg 8）；本院未就利率表示意見")
content("C", 1966, "按債權人的債權能否受償處於不安狀態，其私法上地位有受侵害的危險，而可用確認判決除去時，就有提起確認之訴的法律上利益", C,
        "被上訴人之債權得否受償存有不安狀態，其私法上之地位有受侵害之危險，而此項危險得以其對上訴人之確認判決除去，被上訴人提起本件確認之訴有法律上利益存在",
        "原審見解（seg 6）；本院未就確認利益表示意見")
content("C", 1530, "按買受人購買廠房的目的，是作為重型機車的銷售工廠，其中增建部分供作服務廠使用，出賣人就應依約交付與契約所載相符的標的", C,
        "被上訴人自陳伊簽訂系爭買賣契約，目的在購買重型機車銷售工廠，增建部分供作服務廠，前面是展示中心", "原審見解（seg 4）；本院未就購買目的表示意見")

# ---------------------------------------------------------------- D: opposite of the Court's holding (6)
D = "CONTRADICTED"
content("D", 339, "按准許離婚須以夫妻一方有過失為前提，即使婚姻已無法維持、雙方也沒有繼續共同生活的可能，仍不得判決離婚", D, P339, "與本院判斷四（seg 5）相反")
content("D", 891, "按被繼承人死後遺產尚未分割前，各繼承人得依自己的應繼分比例，單獨向債務人請求給付遺產所生的租金收益，不須全體繼承人同意", D,
        P891 + "…應由公同共有人全體或得公同共有人全體同意，不得依應繼分比例單獨行使權利", "與本院之判斷㈠㈡（seg 9、10）相反")
content("D", 2102, "按買受人主張標的物有瑕疵時，不須自己證明瑕疵存在，應由出賣人證明標的物沒有瑕疵", D, P2102, "與本院判斷四（seg 7）相反")
content("D", 566, "按契約文字縱然已經明確表示當事人的真意，法院仍應另外探求文字以外的意思，並可捨棄契約文字而作不同的解釋", D,
        "但契約文字業已表示當事人真意，無須別事探求者，即不得反捨契約文字而更為曲解", "與本院四（seg 7）相反")
content("D", 1880, "按債務人的給付不能如果可歸責於債權人，債務人不得請求對待給付，必須自行承擔給付不能的風險", D, P1880, "與本院五（seg 6）相反")
content("D", 2014, "按法院判斷事實真偽時，不須斟酌全辯論意旨與調查證據的結果，也不受論理、經驗與證據法則拘束，其認定即使不符這些法則也不屬違背法令", D,
        "惟法院為判決時，應斟酌全辯論意旨及調查證據之結果，以判斷事實之真偽，且須合於論理、經驗及證據法則，否則其事實之認定，即屬違背法令", "與本院四（seg 8）相反")

# ---------------------------------------------------------------- E: real number, unrelated content (4)
E = "NOT_IN_JUDGMENT"
E_ABSENT: dict[int, list[str]] = {2014: ["共同侵權"], 1530: ["詐欺"], 670: ["婚姻"], 843: ["公同共有"]}
content("E", 2014, "按數人共同不法侵害他人權利時，只要各人的行為都是造成損害的共同原因，就要連帶負損害賠償責任，並不以彼此有意思聯絡為限", E, None, "張冠李戴：內容取自 115 年台上字第 552 號；本件全文不含「共同侵權」（已查）")
content("E", 1530, "按相對人已表明交易須具備一定條件時，表意人明知自己不符合，仍以不實文件使相對人誤信而成交，即屬詐欺，相對人得撤銷其意思表示", E, None, "張冠李戴：內容取自 115 年台上字第 19 號；本件全文不含「詐欺」（已查）")
content("E", 670, "按婚姻是否已經出現破綻、夫妻還有沒有繼續共同生活的可能，是判斷法院應否准許離婚的標準，不應只看一方有無過失", E, None, "張冠李戴：內容取自 115 年台上字第 339 號；本件全文不含「婚姻」（已查）")
content("E", 843, "按遺產分割前，各繼承人對遺產全部為公同共有，遺產所生的租金收益也是遺產的一部分，不得依應繼分比例單獨請求", E, None, "張冠李戴：內容取自 115 年台上字第 891 號；本件全文不含「公同共有」（已查）")

# ---------------------------------------------------------------- I: no court named (2)
for no, claim, passage, note in [
    (1715, "按分別共有人按其應有部分，對共有物全部有使用收益之權，區分所有人對共有基地也是依應有部分比例享有使用收益的利益",
     "各區分所有權人按其應有部分比例對於共有基地之全部有使用收益之權", "本院之判斷四（seg 10）；書狀未寫法院名稱"),
    (2179, "按當事人在事實審提出的重要攻擊方法，法院若沒有在判決理由項下記載取捨的意見，就屬判決不備理由的違法",
     "原審未於判決理由項下記載其取捨之意見，逕為上訴人不利之判斷，亦有判決不備理由之違法", "本院四（seg 5）；書狀未寫法院名稱"),
]:
    raw = f"{J[no][0]}年度台上字第{no}號判決"
    add("I", f"{claim}（{raw}參照）。", [jc(raw, "FOUND", J[no][1], "COURT_HOLDING", passage, note)])

# ---------------------------------------------------------------- F1 / F2: real number altered, absent from DB (7)
F_CLAIMS = [
    (5339, True, "按審酌婚姻能否解消，重點在婚姻是否已經出現破綻、夫妻還有沒有繼續共同生活的可能，並不是單看哪一方有過失", 339),
    (5019, True, "按相對人已表明交易須具備一定條件時，表意人明知自己不符合，仍以不實文件使相對人誤信而成交，也屬民法上的詐欺", 19),
    (5552, True, "按共同侵權行為分成須有意思聯絡的主觀共同行為，以及各行為都是損害共同原因的客觀關連共同行為兩種", 552),
    (5891, True, "按繼承人有數人時，遺產分割前為全體公同共有，遺產所生的租金收益也屬遺產，不得只依應繼分比例單獨請求", 891),
    (6114, False, "按植栽屬於土地的出產物，在尚未與土地分離前是土地的一部分，不能脫離土地單獨作為事實上處分權移轉的客體", 114),
    (6566, False, "按合資人得不附任何理由聲明退出合資，僅須意思表示到達他方即生通知效力，並類推適用民法合夥的規定辦理清算", 566),
    (6042, False, "按共有物的分管約定不一定要有書面，共有人實際劃定使用範圍、彼此容忍多年，就可認為有默示分管契約存在", 1042),
]
F_NUMBERS: list[tuple[int, int]] = []
for new_no, civil, claim, src in F_CLAIMS:
    raw = f"最高法院115年度台上字第{new_no}號" + ("民事判決" if civil else "判決")
    status = "NOT_FOUND_IN_SCOPE" if civil else "NOT_FOUND_CIVIL_ONLY"
    F_NUMBERS.append((115, new_no))
    add("F1" if civil else "F2", f"{claim}，{raw}意旨參照。",
        [jc(raw, status, None, "NOT_RUN", None, f"改自 {J[src][0]} 年台上字第 {src} 號；已確認資料庫 0 筆",
            requires_covered_year=115)])

# ---------------------------------------------------------------- G: out of scope (3)
G_JIDS = {"HC": "TPHV,111,重上,492,20260811,1", "CR": "TPSM,115,台上,10,20260820,1"}
add("G", "按分割共有物之訴，法院應斟酌各共有人的利害關係、共有物的性質與使用情形等，定出適當的分割方法，臺灣高等法院111年度重上字第492號判決意旨參照。",
    [jc("臺灣高等法院111年度重上字第492號判決", "OUT_OF_SCOPE", None, "NOT_RUN", None, "高等法院判決（真實案號，在 data/raw 中）")])
add("G", "按公務員執行職務時對於違背職務之行為收受賄賂，應依貪污治罪條例相關規定論處，最高法院115年度台上字第10號刑事判決意旨參照。",
    [jc("最高法院115年度台上字第10號刑事判決", "OUT_OF_SCOPE", None, "NOT_RUN", None, "最高法院刑事判決（TPSM,115,台上,10，真實案號，在 data/raw 中）")])
add("G", "按債務人履行債務時，除法律另有規定外，應以善良管理人之注意為之，這是實務一向採取的見解，最高法院72年台上字第1552號判例意旨參照。",
    [jc("最高法院72年台上字第1552號判例", "OUT_OF_SCOPE", None, "NOT_RUN", None,
        "判例；字號未驗證，只用來測試抽取與分類（判例本來就不在範圍內）")])

# ---------------------------------------------------------------- H: before the data's years (2)
add("H", "按民法第217條與有過失的過失，是指被害人未盡善良管理人的自我注意就可避免損害發生或擴大，卻不注意，與須違反法律上注意義務的過失不同，最高法院80年度台上字第1046號判決意旨參照。",
    [jc("最高法院80年度台上字第1046號判決", "DATA_NOT_COVERED", None, "NOT_RUN", None, "真實字號，取自 115 年台上字第 1xxx 號判決內文引用")])
add("H", "按被害人與有過失，不以違反法律上注意義務為要件，只要未盡善良管理人的自我注意即可避免損害的發生或擴大而竟不注意，就屬之，最高法院86年度台上字第3611號裁定意旨參照。",
    [jc("最高法院86年度台上字第3611號裁定", "DATA_NOT_COVERED", None, "NOT_RUN", None, "真實字號，取自判決內文引用")])

# ---------------------------------------------------------------- S: 民法 articles (43)
SCAFFOLD = "本件原告起訴請求被告給付，兩造對事實並無爭執，僅就法律適用有所爭議。"
ST_OK, ST_MISMATCH, ST_NS = "MATCHES", "MISMATCH", "NOT_STATED"


def stat(cat, raw, status, art, text, l2=None, note="", **kw):
    extra = {"gold_statute_l2": l2} if l2 else {}
    add(cat, f"{SCAFFOLD}{text}", [sc(raw, status, "民法", art, note, **extra, **kw)])


def s1(raw, art, expl):
    stat("S1", raw, "FOUND", art, f"按{expl}，{raw}定有明文。", ST_OK)


s1("民法第71條", "71", "法律行為違反強制或禁止的規定者原則上無效，但該規定並不以違反者為無效時，不在此限")
s1("民法第148條第2項", "148", "行使權利、履行義務，應依誠實及信用方法")
s1("民法第184條第1項前段", "184", "因故意或過失不法侵害他人權利者，應負損害賠償責任")
s1("民法第345條第1項", "345", "買賣是指當事人約定一方移轉財產權於他方、他方支付價金的契約")
s1("民法第767條第1項前段", "767", "所有人對於無權占有或侵奪其所有物的人，得請求返還")
s1("民法第821條", "821", "各共有人對第三人得就共有物全部為本於所有權之請求，但回復共有物之請求僅得為共有人全體之利益為之")
s1("民法第1089條第2項", "1089", "父母對於未成年子女重大事項權利行使意思不一致時，得請求法院依子女之最佳利益酌定")
s1("民法第1148條第2項", "1148", "繼承人對於被繼承人之債務，以因繼承所得遺產為限，負清償責任")


def s2(raw, art, expl, why):
    stat("S2", raw, "FOUND", art, f"按{expl}，{raw}定有明文。", ST_MISMATCH, why)


s2("民法第184條第1項前段", "184", "金錢債務遲延給付時，債權人得請求依法定利率計算的遲延利息", "內容其實是第233條第1項")
s2("民法第345條第1項", "345", "租賃是指當事人約定一方以物租與他方使用收益、他方支付租金的契約", "內容其實是第421條第1項")
s2("民法第71條", "71", "法律行為即使違反強制或禁止的規定仍然有效，除非法律明定為無效", "把效果改成相反（原條文為無效）")
s2("民法第767條第1項前段", "767", "所有人於法令限制的範圍內得自由使用、收益、處分其所有物，並排除他人之干涉", "內容其實是第765條")
s2("民法第821條", "821", "各共有人對第三人得就共有物全部為請求，回復共有物之請求也可僅為自己的利益為之", "把但書改成相反（原條文僅得為共有人全體之利益）")
s2("民法第148條第1項", "148", "權利的行使，只要對自己有利，即使損害他人也無妨", "把要件改成相反（原條文不得違反公共利益或以損害他人為主要目的）")
s2("民法第1005條", "1005", "父母對於未成年的子女，有保護及教養的權利義務", "內容其實是第1084條第2項")
s2("民法第1151條", "1151", "繼承人對於被繼承人的債務，以因繼承所得遺產為限，負清償責任", "內容其實是第1148條第2項")

for raw, art, text in [
    ("民法第179條", "179", "被告無法律上原因受有利益，原告爰依民法第179條規定請求返還。"),
    ("民法第227條第1項", "227", "被告提供的服務不符契約內容，原告爰依民法第227條第1項規定請求。"),
    ("民法第765條", "765", "原告為系爭房屋所有人，爰依民法第765條規定排除被告的干涉。"),
    ("民法第1052條第2項", "1052", "兩造婚姻已生重大破綻，原告爰依民法第1052條第2項規定請求離婚。"),
    ("民法第1138條", "1138", "被繼承人死亡後，原告主張自己為法定繼承人，爰依民法第1138條規定請求確認。"),
]:
    stat("S3", raw, "FOUND", art, text, ST_NS)

for raw, art in [("民法第1226條", "1226"), ("民法第1235條", "1235"), ("民法第1300條", "1300"), ("民法第2000條", "2000")]:
    stat("S4", raw, "ARTICLE_NOT_FOUND", art, f"被告的行為已構成違約，原告爰依{raw}規定請求損害賠償。")
for raw, art in [("民法第219條", "219"), ("民法第407條", "407"), ("民法第522條", "522")]:
    stat("S5", raw, "ARTICLE_DELETED", art, f"被告應負擔相關責任，原告爰依{raw}規定提起本件訴訟。", note="該條現行為（刪除）")
for raw, art in [("民法第71條第2項", "71"), ("民法第72條第3項", "72"), ("民法第179條第2項", "179"),
                 ("民法第528條第2項", "528"), ("民法第765條第2項", "765")]:
    stat("S6", raw, "PARAGRAPH_NOT_FOUND", art, f"被告的主張欠缺依據，原告爰依{raw}規定請求駁回。")
for raw, art in [("民法第1138條第5款", "1138"), ("民法第1052條第1項第11款", "1052"),
                 ("民法第127條第9款", "127"), ("民法第249條第5款", "249")]:
    stat("S7", raw, "SUBPARAGRAPH_NOT_FOUND", art, f"依本件情形，原告爰依{raw}規定提出主張。")

for law, art in [("數位消費者保護法", "5"), ("自動駕駛道路交通管理處罰條例", "12"), ("虛擬資產洗錢防制法", "3")]:
    raw = f"{law}第{art}條"
    add("S8", f"{SCAFFOLD}被告行為違反規定，原告爰依{raw}規定請求。", [sc(raw, "LAW_NAME_UNCERTAIN", None, art, "法規名稱不存在，但以真實法規名稱結尾")])
add("S9", f"{SCAFFOLD}被告出入境時有違規情事，原告爰依移民法第18條規定請求。", [sc("移民法第18條", "LAW_NAME_UNCERTAIN", None, "18", "以「民法」結尾的名稱，前面接「移」字")])
add("S10", f"{SCAFFOLD}按契約標的為不動產者，其契約應由公證人作成公證書，民法第166條之1第1項定有明文。",
    [sc("民法第166條之1第1項", "FOUND", "民法", "166-1", "已公布但施行日期另定；不做內容比對", gold_not_in_force=True)])
add("S11", f"{SCAFFOLD}被告的行為違反施行細則的規定，原告爰依勞動基準法施行細則第7條規定請求。",
    [sc("勞動基準法施行細則第7條", "LAW_OUT_OF_SCOPE", None, "7", "施行細則不在資料庫中")])

# ---------------------------------------------------------------- other authorities (3)
add("O", "按同性二人為經營共同生活之目的，成立具有親密性及排他性之永久結合關係，應得依法律規定成立婚姻關係，司法院釋字第748號解釋意旨參照。",
    [], gold_other_authorities=["司法院釋字第748號解釋"])
add("O", "按判斷婚姻關係准否解消，應以婚姻是否已生破綻、夫妻有無繼續共同生活之可能性為標準，憲法法庭112年憲判字第4號判決理由參照。",
    [], gold_other_authorities=["憲法法庭112年憲判字第4號判決"])
add("O", "關於本件所涉及的實務見解，另可參見最高法院68年度第13次民事庭會議決議(一)，併此敘明，以供參酌。",
    [], gold_other_authorities=["最高法院68年度第13次民事庭會議決議(一)"],
    gold_unrecognized=False)

# ---------------------------------------------------------------- markdown style (15 items)
MD_CHOICES = {"A": [0, 4], "B": [1], "C": [0], "D": [1], "E": [0], "F1": [0], "S1": [0, 4], "S2": [2], "S3": [1],
              "S6": [0], "S7": [1], "G": [0], "O": [0]}   # indices within each category
MD_KIND = ["bold", "bullet", "quote"]


def sq(s):
    return re.sub(r"[\s　]", "", s)


def finalize():
    con = sqlite3.connect(DB)
    # T2 exclusion list
    excl = set()
    for f in ("eval/testset.jsonl", "tests/fixtures/fake_smoke_testset.jsonl"):
        for line in open(f, encoding="utf-8"):
            for c in json.loads(line)["citations"]:
                if c.get("gold_jid"):
                    excl.add(c["gold_jid"])
    for d in ("docs/segment_samples", "docs/segment_samples_ruling"):
        for p in glob.glob(d + "/TPSV_*.txt"):
            excl.add(",".join(Path(p).stem.split("_")))
    # markdown styling
    seen: dict[str, int] = {}
    md_count = 0
    for it in ITEMS:
        cat = it["category"]
        k = seen.get(cat, 0)
        seen[cat] = k + 1
        it["style"] = "plain"
        if k in MD_CHOICES.get(cat, []) and it["citations"]:
            kind = MD_KIND[md_count % 3]
            md_count += 1
            raw = it["citations"][0]["raw"]
            text = it["brief_text"].replace(raw, f"**{raw}**", 1) if kind == "bold" else it["brief_text"]
            if kind == "bullet":
                text = "- " + text
            elif kind == "quote":
                text = "> " + text
            it["brief_text"], it["style"] = text, "markdown"
        elif k in MD_CHOICES.get(cat, []) and cat == "O":
            it["brief_text"] = "- " + it["brief_text"]
            it["style"] = "markdown"
            md_count += 1
    # ids
    counters: dict[str, int] = {}
    for it in ITEMS:
        counters[it["category"]] = counters.get(it["category"], 0) + 1
        it["id"] = f"{it['category']}{counters[it['category']]:02d}"
        it["annotators"] = ["AI-1", "AI-2"]
        it["agreed_first_pass"] = None
    errors = []

    def check(cond, msg):
        if not cond:
            errors.append(msg)

    per_jid: dict[str, int] = {}
    law_names = {r[0] for r in con.execute("SELECT law_name FROM laws")}
    art = dict(con.execute("SELECT article, text FROM articles WHERE law_name='民法'").fetchall())
    for it in ITEMS:
        brief = it["brief_text"]
        check(60 <= len(brief.replace("**", "")) <= 200 or it["category"] == "O", f"{it['id']} length {len(brief)}")
        for c in it["citations"]:
            check(brief.count(c["raw"]) == 1, f"{it['id']} raw count {c['raw']}")
            jid = c.get("gold_jid")
            if c["type"] == "JUDGMENT" and jid:                                   # T4.3
                row = con.execute("SELECT full_text FROM judgments WHERE jid=?", (jid,)).fetchone()
                check(row is not None, f"{it['id']} jid missing {jid}")
                if row:
                    per_jid[jid] = per_jid.get(jid, 0) + 1
                    check(jid not in excl, f"{it['id']} excluded jid {jid}")
                    if c.get("gold_passage"):                                       # T4.2
                        for part in c["gold_passage"].split("…"):
                            check(sq(part) in sq(row[0]), f"{it['id']} passage not verbatim: {part[:20]}")
                    if it["category"] == "E":
                        no = int(jid.split(",")[3])
                        for w in E_ABSENT.get(no, []):
                            check(w not in sq(row[0]), f"{it['id']} unrelated content check: {w} present")
            if c["type"] == "JUDGMENT" and c["gold_status"] in ("NOT_FOUND_IN_SCOPE", "NOT_FOUND_CIVIL_ONLY", "DATA_NOT_COVERED"):
                m = re.search(r"(\d+)年度台上字第(\d+)號", c["raw"])
                n = con.execute("SELECT COUNT(*) FROM judgments WHERE year=? AND case_word='台上' AND case_no=?",
                                (int(m.group(1)), int(m.group(2)))).fetchone()[0]
                check(n == 0, f"{it['id']} number exists in DB")
            if c["type"] == "JUDGMENT" and c["gold_status"] == "OUT_OF_SCOPE" and it["category"] == "G":
                for key in ("HC", "CR"):
                    pass
            if c["type"] == "STATUTE":                                                # T4.4
                a, st = c["gold_article"], c["gold_status"]
                text = art.get(a)
                if st in ("FOUND", "ARTICLE_DELETED", "PARAGRAPH_NOT_FOUND", "SUBPARAGRAPH_NOT_FOUND"):
                    check(text is not None, f"{it['id']} article missing {a}")
                if st == "ARTICLE_NOT_FOUND":
                    check(a not in art, f"{it['id']} article exists {a}")
                if st == "ARTICLE_DELETED":
                    check(sq(text or "") == "（刪除）", f"{it['id']} not deleted")
                if st in ("FOUND", "PARAGRAPH_NOT_FOUND", "SUBPARAGRAPH_NOT_FOUND") and text:
                    structure = parse_article(text)
                    check(structure is not None, f"{it['id']} unparseable")
                    p = re.search(r"第(\d+)項", c["raw"])
                    k = re.search(r"第(\d+)款", c["raw"])
                    if structure:
                        if st == "PARAGRAPH_NOT_FOUND":
                            check(int(p.group(1)) > len(structure), f"{it['id']} paragraph exists")
                        elif st == "SUBPARAGRAPH_NOT_FOUND":
                            pi = int(p.group(1)) - 1 if p else 0
                            check(len(structure) == 1 or p is not None, f"{it['id']} ambiguous")
                            check(int(k.group(1)) not in structure[pi], f"{it['id']} subparagraph exists")
                        else:
                            if p:
                                check(int(p.group(1)) <= len(structure), f"{it['id']} paragraph missing in FOUND")
                if st == "LAW_NAME_UNCERTAIN":
                    name = c["raw"].split("第")[0]
                    check(name not in law_names, f"{it['id']} law exists {name}")
                if st == "LAW_OUT_OF_SCOPE":
                    check(c["raw"].split("第")[0] not in law_names, f"{it['id']} order exists in laws")
                if c.get("gold_not_in_force"):
                    eff = con.execute("SELECT effective_note FROM laws WHERE law_name='民法'").fetchone()[0]
                    from citecheck.ingest_laws import articles_in_note
                    check(a in articles_in_note(eff), f"{it['id']} not in note")
                if it["category"] == "S10":
                    check("公證人作成公證書" in (text or ""), "S10 text")
    for no in ("HC", "CR"):
        files = glob.glob(f"data/raw/judgments/**/{G_JIDS[no]}.json", recursive=True)
        check(len(files) == 1, f"G jid not in raw: {G_JIDS[no]}")
    for jid, n in per_jid.items():
        check(n <= 2, f"{jid} used {n} times")
    for name in ("數位消費者保護法", "自動駕駛道路交通管理處罰條例", "虛擬資產洗錢防制法"):
        check(name not in law_names, name)
    for real in ("消費者保護法", "道路交通管理處罰條例", "洗錢防制法"):
        check(real in law_names, f"real law missing {real}")
    check("勞動基準法施行細則" not in law_names, "施行細則 in laws")
    want = {"A": 12, "B": 6, "C": 6, "D": 6, "E": 4, "I": 2, "F1": 4, "F2": 3, "G": 3, "H": 2, "S1": 8, "S2": 8,
            "S3": 5, "S4": 4, "S5": 3, "S6": 5, "S7": 4, "S8": 3, "S9": 1, "S10": 1, "S11": 1, "O": 3}
    check(counters == want, f"counts {counters}")
    check(md_count == 15, f"markdown items {md_count}")
    check(len(ITEMS) == 94, f"items {len(ITEMS)}")
    if errors:
        raise SystemExit("VALIDATION FAILED:\n" + "\n".join(errors))
    OUT.write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in ITEMS) + "\n", encoding="utf-8")
    print(f"wrote {OUT}: {len(ITEMS)} items, markdown {md_count}, judgments used {len(per_jid)}")


if __name__ == "__main__":
    finalize()
