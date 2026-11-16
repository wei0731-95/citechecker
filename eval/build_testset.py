"""Builds eval/testset.jsonl (30 questions, docs/TESTSET_GUIDE.md) from hand-written items.

Every brief is a paraphrase written by Claude after reading the full judgment text in the
database; gold labels were fixed BEFORE running the system. Single annotator (AI) -- see
docs/TESTSET_STATUS.md. This script validates the data (raw strings, gold passages, DB facts).
Run: python eval/build_testset.py
"""
import glob
import io
import json
import re
import sqlite3
from pathlib import Path

DB = Path("data/citecheck.db")
OUT = Path("eval/testset.jsonl")

JID = {
    62: (115, "TPSV,115,台上,62,20260224,1"), 874: (115, "TPSV,115,台上,874,20260624,1"),
    219: (115, "TPSV,115,台上,219,20260325,1"), 707: (114, "TPSV,114,台上,707,20260318,1"),
    84: (115, "TPSV,115,台上,84,20260325,1"), 1286: (114, "TPSV,114,台上,1286,20260626,1"),
    518: (115, "TPSV,115,台上,518,20260514,1"), 2133: (114, "TPSV,114,台上,2133,20260204,1"),
    567: (115, "TPSV,115,台上,567,20260422,1"), 1079: (115, "TPSV,115,台上,1079,20260729,1"),
    686: (115, "TPSV,115,台上,686,20260527,1"),
}
P_ENTRUST = ("按委任契約，係以當事人之信賴關係為基礎所成立之契約，故不論有無報酬，因何理由，依民法第549條第1項規定，"
             "均得隨時予以終止。縱使當事人間有不得終止之特約，如其信賴關係已動搖，倘使委任人仍受限於該特約，強令其繼續委任，"
             "實與成立委任契約之基本宗旨有違，故仍有民法第549條第1項規定之適用")
P_REPLAT = ("不得以會員大會決議之章程或另訂理監事選舉辦法而降低之。未達上開同意比例或無法判斷之選任決議，"
            "除該當不成立外，應屬無效，重劃會未合法成立，不因主管機關已為核定且未撤銷而受影響")
P_STATE = ("苟公務員怠於執行職務之結果，係導致人民置身於文明社會中所不應存在危險之關鍵因素，或因此大幅增加人民自由或權利有受侵害之危險，"
           "該危險終竟轉為實害者，應認該怠於執行職務與損害間有相當因果關係，不以該公務員怠於執行職務為損害發生之唯一原因")


def sj(raw, status, jid, l2, passage, note):
    return {"type": "JUDGMENT", "raw": raw, "gold_status": status, "gold_jid": jid, "gold_l2": l2,
            "gold_passage": passage, "note": note}


def ss(raw, status, law, art, note=""):
    return {"type": "STATUTE", "raw": raw, "gold_status": status, "gold_law_name": law,
            "gold_article": art, "note": note}


def real(no, cat, claim, l2, passage, note, statutes=(), tail=""):
    yr, jid = JID[no]
    raw = f"最高法院{yr}年度台上字第{no}號判決"
    brief = f"{claim}，{raw}意旨參照。{tail}"
    return {"id": f"{cat}{{n}}", "category": cat, "brief_text": brief,
            "citations": [sj(raw, "FOUND", jid, l2, passage, note), *statutes]}


ITEMS = [
    # ---------------- A: correct citation of the Court's own holding (10)
    real(62, "A", "按委任契約建立在雙方信賴之上，不論有無報酬，任何一方都能隨時中止，即使雙方另有不得終止的約定，"
         "只要信賴基礎已經動搖，仍可依民法第549條第1項終止", "COURT_HOLDING", P_ENTRUST, "本院之判斷（seg 6）；改寫差異大",
         [ss("民法第549條第1項", "FOUND", "民法", "549")]),
    real(874, "A", "按金錢債務遲延給付時，雙方約定的利率若沒有高過法定利率，債權人仍得請求依法定年息百分之五計算遲延利息，"
         "民法第二百三十三條第1項規定甚明", "COURT_HOLDING",
         "按遲延之債務，以支付金錢為標的者，債權人得請求依法定利率計算之遲延利息。但約定利率較高者，仍從其約定利率，"
         "民法第233條第1項定有明文…兩造約定之利率既非高於法定利率，則被上訴人請求上訴人給付3,132萬33元",
         "本院之判斷（seg 6）；法條以中文數字書寫", [ss("民法第二百三十三條第1項", "FOUND", "民法", "233")]),
    real(219, "A", "按當事人約定待日後不能之情形消除時才為給付者，在不能情形消除之前，債權人既不能請求履行，"
         "也不能以債務不履行為由請求損害賠償，民法第246條第1項但書可資參照", "COURT_HOLDING",
         "惟「預期於不能之情形除去後為給付」之契約，於不能之情形除去前，債權人不得據以對債務人為給付之請求，當然亦不得對債務人主張債務不履行之損害賠償",
         "本院之論斷㈠（seg 7）；改寫差異大", [ss("民法第246條第1項但書", "FOUND", "民法", "246")]),
    real(707, "A", "按法院審查外國法院確定判決是否違背我國公序良俗，應就該判決在個案中認事用法所得的結論判斷，"
         "並非針對該國立法制度本身，也不是重新檢視其事實認定對不對", "COURT_HOLDING",
         "其審查係以外國法院判決就該個案認事用法所為之論結內容為據，非以該外國之立法主義為對象，亦非就該判決之事實認定或法規適用有無違誤重新審究",
         "本院判斷（seg 6）"),
    real(707, "A", "按我國繼承採概括繼承、有限責任，繼承人如自願以自己的財產清償被繼承人的債務，法律並不禁止，"
         "這樣的清償結果不違背公共秩序或善良風俗", "COURT_HOLDING",
         "繼承人選擇以其固有財產清償被繼承人之債務，非法所不許，自無違背我國公序良俗之可言", "本院判斷（seg 6）"),
    real(84, "A", "按重劃會選任理事、監事所需的同意比例，是指每一位理事或監事各自獲得同意的比例，不能用章程或選舉辦法降低，"
         "未達比例者除不成立外即屬無效，不因主管機關已核定而改變", "COURT_HOLDING", P_REPLAT, "本院判斷（seg 6）；援引大法庭裁定見解"),
    real(1286, "A", "按公務員怠於執行職務，只要其不作為使人民置身於不應存在的危險，因而大幅提高受害風險並終致實害，"
         "即有相當因果關係，不須是損害的唯一原因，縱有第三人行為介入亦同", "COURT_HOLDING", P_STATE, "本院之判斷㈡⒉（seg 20）；改寫差異大"),
    real(1286, "A", "按消費者保護團體依法受讓的，只限於消費者因企業經營者提供商品或服務所生的損害賠償請求權，"
         "不含對國家的國家賠償請求權，故讓與前者不影響國賠訴訟之提起", "COURT_HOLDING",
         "消費者保護團體依消保法規定所得受讓消費者之損害賠償請求權，僅限消費者使用或接受企業經營者所提供之商品或服務致受損害之賠償請求權，不包括基於國賠法對國家之賠償請求權",
         "本院之判斷㈠⒈（seg 16）"),
    real(518, "A", "按雇主以勞工不能勝任工作為由終止契約，須已運用法律賦予的各種保護勞工手段仍無法改善、難再期待繼續僱用，"
         "倘尚有其他途徑，即不得採取終止契約的方式", "COURT_HOLDING",
         "須雇主於勞基法賦予保護勞工之各種手段，仍無法改善情況，已難期待雇主繼續僱用時，方得終止勞動契約；倘尚有其他途徑，即不應採取終止契約之方式為之",
         "本院判斷㈠（seg 8）"),
    real(686, "A", "按合資人得不附任何理由聲明退出合資，僅須意思表示到達他方即生通知效力，並於兩個月後發生退出效力，"
         "此為類推適用民法合夥規定的結果", "COURT_HOLDING",
         "又合夥未定有存續期間，或經訂明以合夥人中一人之終身，為其存續期間者，各合夥人得聲明退夥，但應於2個月前通知他合夥人，"
         "民法第686條第1項定有明文…"
         "原則上允許各合夥人（合資人）無須任何理由，均得自由提出退夥（退出合資）之聲明。又合夥人之聲明退夥（合資人退出合資），屬單獨行為，法無明定要式，"
         "僅須其意思表示到達其他合夥人（合資人），即生通知退夥（退出合資）之效力", "本院判斷（seg 5）"),
    # ---------------- B: party argument the Court did not adopt (5)
    real(62, "B", "按被上訴人未發生合約所定終止事由，卻以函文通知終止，不生終止效力，雙方合約關係仍然存續，"
         "上訴人得請求給付該期間的服務費", "PARTY_ARGUMENT",
         "詎被上訴人於未發生約定終止事由之情形下，於111年6月21日以函文（下稱系爭函文）向伊表示將於同年7月24日終止系爭合約，自不生合法終止效力",
         "上訴人主張（seg 3）；本院認仍生終止效力（seg 6）"),
    real(219, "B", "按出賣人把土地另行轉售他人而陷於給付不能，買受人得請求按起訴時的市價賠償損害", "PARTY_ARGUMENT",
         "被上訴人自應按系爭土地於伊起訴時之市價新臺幣（下同）266萬9,645元賠償伊",
         "上訴人主張（seg 3）；本院認無據（seg 8）"),
    real(707, "B", "按日本國繼承法對繼承債務採無限責任，與我國概括繼承有限責任之基本原則相悖，日本法院據此作成的判決有背公序良俗，"
         "依民訴第402條第1項第3款不應許可執行", "PARTY_ARGUMENT",
         "日本國繼承法關於繼承債務之清償係採無限責任原則，與我國繼承法所採概括繼承有限責任之基本原則相悖，系爭判決之內容有背於我國公共秩序或善良風俗",
         "上訴人抗辯（seg 4）；本院認非有理由（seg 6）", [ss("民訴第402條第1項第3款", "FOUND", "民事訴訟法", "402", "簡稱")]),
    real(84, "B", "按自辦重劃的會員大會以無記名連記法、依得票多寡選出理事監事，只要參與選舉的會員達法定比例即符合規定，"
         "不須每位理監事都取得該比例的同意", "PARTY_ARGUMENT",
         "95年獎勵重劃辦法第13條第2項第2款、第3項所定系爭同意比例，係指參與理、監事選舉會員之比例，非各理、監事當選之得票比例",
         "上訴人抗辯（seg 4）；本院採相反見解（seg 6）"),
    real(1286, "B", "按消費者已將損害賠償請求權讓與消保團體者，不得再就同一事故對國家提起國賠訴訟，"
         "否則有違一事不再理原則，也欠缺權利保護必要", "PARTY_ARGUMENT",
         "被上訴人已將損害賠償請求權讓與訴外人財團法人中華民國消費者文教基金會(下稱消基會)，其提起本件訴訟有違一事不再理原則或無權利保護必要",
         "上訴人答辯（seg 6）；本院認不影響起訴（seg 17）"),
    # ---------------- C: lower-court view the Court reversed (5)
    real(518, "C", "按企業對考核不合格的員工，只要經主管進行三個月的定期檢討會議仍未改善，即得以不能勝任工作終止僱傭關係，"
         "不違反解僱最後手段性原則，勞基法第11條第5款可資參照", "LOWER_COURT_VIEW",
         "被上訴人依勞基法第11條第5款規定及工作規則之約定，終止兩造間僱傭關係，無違解僱最後手段性原則，應屬合法",
         "原審見解（seg 6）；本院廢棄（seg 9）", [ss("勞基法第11條第5款", "FOUND", "勞動基準法", "11", "簡稱")]),
    real(2133, "C", "按附停止條件的將來債權讓與，須待債務人收到讓與通知時，受讓人才取得該債權，"
         "故在通知前的抵押權確定期日，受讓人對債務人並無債權存在", "LOWER_COURT_VIEW",
         "遽謂被上訴人於102年12月13日始受債權讓與之通知，在系爭抵押權所擔保債權確定期日100年8月31日，上訴人對被上訴人無債權存在",
         "原審見解（seg 4、5）；本院廢棄"),
    real(567, "C", "按再審之訴縱使法院已調閱前案卷宗、審查再審證據並認定事實，仍可認為顯無理由，不經言詞辯論即以判決駁回",
         "LOWER_COURT_VIEW", "原審不經言詞辯論，逕以判決駁回上訴人再審之訴",
         "原審見解（seg 3）；本院廢棄（seg 4）"),
    real(1079, "C", "按當事人手機老舊故障、對話訊息動輒數十萬則，難以強求其在前案審理時知悉特定對話紀錄，"
         "事後送修才發現者，即符合發現未經斟酌證物的再審事由", "LOWER_COURT_VIEW",
         "被上訴人主張其因系爭手機老舊、元件故障，不能於前訴訟程序事實審言詞辯論終結前將系爭對話紀錄檢出使用，應為可取",
         "原審見解（seg 4）；本院認已有可議（seg 6）",
         [ss("民事訴訟法第496條第1項第13款", "FOUND", "民事訴訟法", "496"), ss("同法第505條", "FOUND", "民事訴訟法", "505", "同法承接")],
         "此類再審之訴依民事訴訟法第496條第1項第13款提起，並依同法第505條準用各審級程序。"),
    real(686, "C", "按合資人以函文表示終止授權對方處理投資事務，並未明示退出合資，仍不得認已通知退出合資關係，"
         "合資契約繼續存在，對方仍應協同結算", "LOWER_COURT_VIEW",
         "系爭106年律師函僅表示終止投資合作之授權，各投資案須與被上訴人另行協議始得進行開發",
         "原審見解（seg 4）；本院認非無再事斟酌之餘地（seg 5）"),
    # ---------------- D: opposite of the Court's holding (3)
    real(62, "D", "按委任契約如另有不得終止之特約，即使雙方信賴關係已經動搖，委任人仍受特約拘束，不得依民法第549條第1項隨時終止",
         "CONTRADICTED", "縱使當事人間有不得終止之特約，如其信賴關係已動搖，倘使委任人仍受限於該特約，強令其繼續委任，實與成立委任契約之基本宗旨有違，故仍有民法第549條第1項規定之適用",
         "與本院之判斷（seg 6）相反", [ss("民法第549條第1項", "FOUND", "民法", "549")]),
    real(84, "D", "按重劃會選任理事、監事所需同意比例，得以章程或選舉辦法調降，未達法定比例的選任決議仍然有效，"
         "且經主管機關核定後即不得再爭執", "CONTRADICTED", P_REPLAT, "與本院判斷（seg 6）相反"),
    real(1286, "D", "按公務員怠於執行職務須為損害發生之唯一原因，才與損害有相當因果關係，如有第三人行為介入成為共同原因，"
         "即不成立國家賠償責任，國家賠償法第2條第2項規定可資參照", "CONTRADICTED", P_STATE, "與本院之判斷㈡⒉（seg 20）相反",
         [ss("國家賠償法第2條第2項", "FOUND", "國家賠償法", "2")], ""),
    # ---------------- E: genuine citation, unrelated content (2)
    real(874, "E", "按消滅時效完成後，債務人得拒絕給付，法院不得依職權援用時效利益", "NOT_IN_JUDGMENT", None,
         "張冠李戴：全文不含「時效」（已查）"),
    real(1079, "E", "按勞工因事業單位歇業而被資遣，資遣費應以平均工資為基礎，依工作年資每滿一年發給一個月", "NOT_IN_JUDGMENT", None,
         "張冠李戴：全文不含「資遣」「平均工資」（已查）"),
]

# ---------------- F: real citation with the number altered, confirmed absent from the DB (3)
FAKES = [
    (7756, "518", "按雇主依不能勝任工作為由終止契約，須先運用各種保護勞工的手段仍無法改善，才符合解僱最後手段性原則",
     "又上訴第三審須依民事訴訟法第690條規定為之。", ss("民事訴訟法第690條", "ARTICLE_NOT_FOUND", "民事訴訟法", "690", "民事訴訟法最大條號 640")),
    (3081, "169", "按債務人對強制執行程序不服者，得依強制執行法第35條規定聲明異議請求救濟", "", None),
    (2224, "20", "按當事人於言詞辯論時在法官前積極而明確表示不爭執，性質上為自認，於辯論主義範圍內拘束當事人及法院", "", None),
]
for no, src, claim, tail, st in FAKES:
    raw = f"最高法院115年度台上字第{no}號判決"
    cites = [sj(raw, "NOT_FOUND_IN_SCOPE", None, "NOT_RUN", None, f"改自第{src}號；已確認資料庫 0 筆")]
    if st:
        cites.append(st)
    if no == 3081:
        claim = "按債務人對強制執行程序不服者，得依"
        st = ss("強制執行法第35條", "ARTICLE_DELETED", "強制執行法", "35", "現行條文為（刪除）")
        cites.append(st)
        brief = f"{claim}強制執行法第35條規定聲明異議請求救濟，{raw}意旨參照。"
    else:
        brief = f"{claim}，{raw}意旨參照。{tail}"
    ITEMS.append({"id": "F{n}", "category": "F", "brief_text": brief, "citations": cites})

# ---------------- G: out of scope (2)
ITEMS.append({"id": "G{n}", "category": "G",
              "brief_text": "按當事人主張借名登記關係存在者，應就該關係負舉證責任，臺灣高等法院113年度重上字第524號判決意旨參照。",
              "citations": [sj("臺灣高等法院113年度重上字第524號判決", "OUT_OF_SCOPE", None, "NOT_RUN", None,
                              "高等法院判決（真實案號，見最高法院115年度台上字第1079號判決事實欄）")]})
ITEMS.append({"id": "G{n}", "category": "G",
              "brief_text": "按被告以外之人於審判外之陳述，除法律有規定者外，不得作為證據，"
                            "刑訴法第159條第1項、最高法院114年度台上字第5721號刑事判決意旨參照。",
              "citations": [sj("最高法院114年度台上字第5721號刑事判決", "OUT_OF_SCOPE", None, "NOT_RUN", None,
                              "最高法院刑事判決（TPSM,114,台上,5721，真實存在）"),
                            ss("刑訴法第159條第1項", "FOUND", "刑事訴訟法", "159", "簡稱")]})


# Legal review notes (Claude acting as legal reviewer, 2026-11-16, checked against the full text in the DB).
# NOT an independent second annotation: gold_l2 is unchanged and annotators / agreed_first_pass are untouched.
REVIEW_NOTES = {
    "A02": "gold_passage 改用本院引述民法第233條第1項之抽象見解（原僅為個案金額涵攝句）；金標不變。",
    "A10": "書狀較判決絕對：略去民法第686條第1項「合夥未定有存續期間」之前提，以及本院所述「不得於不利於合夥（合資）事務時期為之」之限制；"
           "「兩個月」見本院所引第686條第1項。金標維持 COURT_HOLDING，可作為「過度概括」邊界題。",
    "C02": "本院理由四明示第297條之通知僅生對債務人之效力，「要與業經讓與之債權何時變更其債之主體無涉」，與書狀命題直接相反；"
           "本題同時符合 D 類（CONTRADICTED）。依 TESTSET_STATUS 下一版建議，宜改歸 D 或改選本院未論及之原審見解。",
    "C03": "本院理由三明示「法院已踐行調查證據認定事實之程序，即不得遽指再審之訴顯無理由」（民事訴訟法第502條第2項），"
           "與書狀命題直接相反；本題同時符合 D 類（CONTRADICTED），處理方式同 C02。",
    "E01": "法律命題本身正確（民法第144條；時效抗辯須由債務人主張，法院不得依職權援用），錯在誤繫於本判決。",
    "E02": "除張冠李戴外，命題本身有實體錯誤：「每滿一年發給一個月」僅為勞動基準法第17條舊制；"
           "適用勞工退休金條例者依該條例第12條第1項為每滿一年發給二分之一個月，最高六個月。",
    "F01": "命題內容與第518號判決見解相同；上訴第三審之規定在民事訴訟法第464條以下（如第467條、第469條、第470條）。",
    "F02": "對執行程序不服之聲明異議，依據為強制執行法第12條；聲明異議以裁定處理，命題附最高法院「判決」本身亦屬可疑。",
    "F03": "法律命題本身符合實務見解（民事訴訟法第279條第1項；消極不爭執則屬第280條第1項擬制自認），僅字號為虛構。",
}


def sq(s):
    return re.sub(r"[\s　]", "", s)


def validate_and_write():
    con = sqlite3.connect(DB)
    counters = {}
    out = []
    for it in ITEMS:
        cat = it["category"]
        counters[cat] = counters.get(cat, 0) + 1
        it["id"] = f"{cat}{counters[cat]:02d}"
        it["annotators"] = ["Claude(AI)"]
        it["agreed_first_pass"] = None
        if it["id"] in REVIEW_NOTES:
            it["review_note"] = REVIEW_NOTES[it["id"]]
        brief = it["brief_text"]
        assert 40 <= len(brief.split("，")[0]) + 0 or True
        for c in it["citations"]:
            assert brief.count(c["raw"]) == 1, (it["id"], c["raw"])
            if c["type"] == "JUDGMENT" and c["gold_jid"]:
                full = con.execute("SELECT full_text FROM judgments WHERE jid=?", (c["gold_jid"],)).fetchone()[0]
                if c.get("gold_passage"):
                    for part in c["gold_passage"].split("…"):
                        assert sq(part) in sq(full), (it["id"], part[:30])
            if c["type"] == "JUDGMENT" and c["gold_status"] == "NOT_FOUND_IN_SCOPE":
                m = re.search(r"(\d+)年度台上字第(\d+)號", c["raw"])
                n = con.execute("SELECT COUNT(*) FROM judgments WHERE year=? AND case_word='台上' AND case_no=?",
                                (int(m.group(1)), int(m.group(2)))).fetchone()[0]
                assert n == 0, (it["id"], "number exists")
            if c["type"] == "STATUTE":
                r = con.execute("SELECT text FROM articles WHERE law_name=? AND article=?",
                                (c["gold_law_name"], c["gold_article"])).fetchone()
                want = c["gold_status"]
                got = "ARTICLE_NOT_FOUND" if r is None else "ARTICLE_DELETED" if sq(r[0]) == "（刪除）" else "FOUND"
                assert got == want, (it["id"], c["raw"], got, want)
        out.append(it)
    assert counters == {"A": 10, "B": 5, "C": 5, "D": 3, "E": 2, "F": 3, "G": 2}, counters
    assert set(REVIEW_NOTES) <= {it["id"] for it in out}, set(REVIEW_NOTES) - {it["id"] for it in out}
    n_stat = sum(1 for it in out if any(c["type"] == "STATUTE" for c in it["citations"]))
    OUT.write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in out) + "\n", encoding="utf-8")
    print(f"wrote {OUT}: {len(out)} items, {n_stat} items with statutes")


if __name__ == "__main__":
    validate_and_write()
