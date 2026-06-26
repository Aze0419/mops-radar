#!/usr/bin/env python3
"""mops_radar 核心邏輯單元測試"""
import re, sys, unittest
sys.path.insert(0, '/Users/iroman/mops_radar')

# 從 mops_radar 直接 import 要測試的函式
from mops_radar import calc_pe, parse_announcement_list, strip_tags

class TestCalcPe(unittest.TestCase):

    def test_monthly_eps_basic(self):
        detail = "每股盈餘 1.83元，年增率 242.86%"
        r = calc_pe(detail, 785.0)
        self.assertAlmostEqual(r['pre_monthly_eps'], 1.83)
        self.assertAlmostEqual(r['pre_annual_eps'], 21.96)
        self.assertAlmostEqual(r['pre_pe'], 35.75, places=1)
        self.assertEqual(r['pre_eps_source'], '月')

    def test_negative_eps_bracket(self):
        detail = "每股盈餘 (0.50)元"
        r = calc_pe(detail, 100.0)
        self.assertAlmostEqual(r['pre_monthly_eps'], -0.5)
        self.assertIsNone(r['pre_pe'])  # 虧損，PE 無意義

    def test_quarterly_captured_as_second(self):
        # nums[0]=月EPS, nums[1]=季EPS；兩個數字都要被抓到
        detail = "每股盈餘 1.83元 年增率 242%\n季每股盈餘 5.49元"
        r = calc_pe(detail, 785.0)
        self.assertAlmostEqual(r['pre_monthly_eps'], 1.83)
        self.assertAlmostEqual(r['pre_quarterly_eps'], 5.49)
        self.assertEqual(r['pre_eps_source'], '月')  # 有月EPS時優先用月

    def test_no_eps(self):
        detail = "本公司無相關資料"
        r = calc_pe(detail, 100.0)
        self.assertIsNone(r['pre_monthly_eps'])
        self.assertIsNone(r['pre_pe'])
        self.assertEqual(r['pre_pe_note'], '無EPS資料')

    def test_revenue_extraction(self):
        detail = "營業收入 773百萬，年增率 146.18%\n每股盈餘 1.68元"
        r = calc_pe(detail, 672.0)
        self.assertIsNotNone(r['pre_monthly_revenue'])
        self.assertIsNotNone(r['pre_monthly_revenue_yoy'])


class TestParseAnnouncementList(unittest.TestCase):

    def _make_form(self, base, code, name, date8, time6, subject, clause_num, fact8, detail):
        """產生模擬 ajax_t05st02 的 <form> HTML"""
        inputs = {
            base+0: name, base+1: code, base+2: date8, base+3: time6,
            base+4: subject, base+6: clause_num, base+7: fact8, base+8: detail,
        }
        hidden = ''.join(
            f'<input type="hidden" name="h{k}" value="{v}">'
            for k, v in inputs.items()
        )
        onclick = (f"SEQ_NO.value='1';document.fm.SPOKE_TIME.value='{time6}';"
                   f"document.fm.SPOKE_DATE.value='{date8}';"
                   f"document.fm.COMPANY_ID.value='{code}'")
        return f'<form><button onclick="{onclick}">{hidden}</button></form>'

    def test_basic_parsing(self):
        html = self._make_form(0, '3167', '大量', '20250625', '070004',
                               '公告每月EPS', '51', '20250620', '每股盈餘1.83元')
        out = parse_announcement_list(html)
        self.assertEqual(len(out), 1)
        ann = out[0]
        self.assertEqual(ann['公司代號'], '3167')
        self.assertEqual(ann['公司名稱'], '大量')
        self.assertEqual(ann['發言日期'], '2025-06-25')
        self.assertEqual(ann['發言時間'], '07:00:04')
        self.assertEqual(ann['符合條款'], '第51款')
        self.assertIn('每股盈餘', ann['說明'])

    def test_onclick_params_extracted(self):
        html = self._make_form(0, '2330', '台積電', '20250625', '120000',
                               '主旨', '51', '20250620', '說明')
        out = parse_announcement_list(html)
        ann = out[0]
        self.assertEqual(ann['_seq_no'], '1')
        self.assertEqual(ann['_spoke_time'], '120000')
        self.assertEqual(ann['_spoke_date'], '20250625')

    def test_multiple_forms(self):
        f1 = self._make_form(0,  '2330', '台積電', '20250625', '070000', '主旨1', '51', '20250620', '說明1')
        f2 = self._make_form(10, '2454', '聯發科', '20250625', '080000', '主旨2', '51', '20250620', '說明2')
        out = parse_announcement_list(f1 + f2)
        self.assertEqual(len(out), 2)
        codes = [a['公司代號'] for a in out]
        self.assertIn('2330', codes)
        self.assertIn('2454', codes)

    def test_empty_html(self):
        out = parse_announcement_list('<html>查無需求資料</html>')
        self.assertEqual(out, [])


class TestStripTags(unittest.TestCase):

    def test_br_to_newline(self):
        self.assertIn('\n', strip_tags('a<br>b'))

    def test_removes_tags(self):
        result = strip_tags('<b>粗體</b>文字')
        self.assertEqual(result, '粗體文字')

    def test_nbsp(self):
        self.assertEqual(strip_tags('a&nbsp;b'), 'a b')


if __name__ == '__main__':
    unittest.main(verbosity=2)
