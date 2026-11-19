import unittest

from eso.bridge import layout_modules_for
from eso.drawing_checks import branch_text_contacts


class BranchReadabilityTests(unittest.TestCase):
    def test_text_over_implicit_branch(self):
        wires = [('a', 'P', (0, 0), (40, 0)), ('b', 'P', (20, 0), (20, 10))]
        def contacts(x, page='P', segments=wires):
            text = {'id': 'name', 'page': page, 'bbox': [x, -1, x + 10, 4]}
            return branch_text_contacts(segments, [], [text])
        self.assertTrue(contacts(15))
        self.assertFalse(contacts(3))
        self.assertFalse(contacts(15, 'P2'))
        self.assertFalse(contacts(15, segments=wires[:1] + [('b', 'P', (20,-10), (20,10))]))
        self.assertFalse(contacts(15, segments=[('a','P',(0,0),(20,0)), wires[1]]))

    def test_primary_rail_does_not_force_unrelated_group_direct(self):
        groups = ['source','control','accessory']
        components = [{'id': i,'ref': i.upper(),'module': i,'pins':[{'number':'1','net':'V'}]} for i in groups]
        plan = {'modules':[{'id':i,'title':i,'core_ref':i.upper()} for i in groups],
                'components': components, 'drawing_groups':[{'id':'joint','title':'power','modules':groups[:2]},
                {'id':'accessory','title':'accessory','modules':groups[2:]}],
                'primary_paths':[{'links':[{'net':'V','from':{'componentId':'source','pinNumber':'1'},
                                            'to':{'componentId':'control','pinNumber':'1'}}]}]}
        a,b = layout_modules_for(plan, {'nets':[{'name':'V','id':'rail','role':'power'}]})
        self.assertEqual(a['netPolicies']['rail'],'direct')
        self.assertEqual(b['netPolicies']['rail'],'local_power')
