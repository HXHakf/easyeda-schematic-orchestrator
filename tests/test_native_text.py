import copy
import inspect
import tempfile
import unittest
from pathlib import Path

from eso.bridge import build_layout_input
from eso.core import Refused, file_digest
from test_bridge import fixture


class NativeTextTests(unittest.TestCase):
    def setUp(self):
        self.assertIn('text_measurements', inspect.signature(build_layout_input).parameters,
                      'native SVG measurement evidence is not accepted yet')

    def setup_source(self, root):
        c,p,b,s,l,g,svg = fixture(root)
        svg.write_text('''<svg xmlns="http://www.w3.org/2000/svg" style="fill:#FFFCF8;">
          <g id="fixture-pin-1" c_partid="part_pin"><circle cx="70" cy="-100" r="1"/></g>
          <g id="fixture-pin-2" c_partid="part_pin"><circle cx="130" cy="-100" r="1"/></g>
          <text id="fixture-designator" x="90" y="-125">R1</text>
          <text id="model" x="120" y="-125">SYNTHETIC</text></svg>''', encoding='utf-8')
        g['result']['sourceSha256'] = file_digest(svg)
        s['result']['pagePrimitives']['attributes'] = [
            dict(primitiveId='fixture-designator', ParentPrimitiveId='fixture-native-part', Key='Designator', Value='R1', KeyVisible=False, ValueVisible=True),
            dict(primitiveId='model', ParentPrimitiveId='fixture-native-part', Key='Name', Value='={Manufacturer Part}', KeyVisible=False, ValueVisible=None),
            dict(primitiveId='metadata', ParentPrimitiveId='fixture-native-part', Key='Supplier', Value='LCSC', KeyVisible=None, ValueVisible=None)]
        measured = dict(source='native-svg-browser-getBBox', svgSha256=file_digest(svg), svgToEdaTransform=[1,0,0,-1,0,0], texts=[
            dict(id='fixture-designator', text='R1', bbox=[90,125,110,135]),
            dict(id='model', text='SYNTHETIC', bbox=[120,125,170,135])])
        return c,p,b,s,l,g,svg,measured

    def test_nullable_raw_attributes_use_rendered_evidence_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            c,p,b,s,l,g,svg,m = self.setup_source(Path(directory))
            original = copy.deepcopy(s)
            source = build_layout_input(c,p,b,s,l,g,svg,text_measurements=m)
            self.assertEqual(s, original)
            self.assertIn(dict(minX=120,minY=125,maxX=170,maxY=135), source['measurements'][0]['textBboxes'])
            from eso.native_text import validate
            _, evidence=validate(s,svg,m)
            self.assertNotIn('textEvidence',source)  # Upstream schema rejects extra fields.
            self.assertEqual(evidence['nullableVisibilityPreserved'], ['model', 'metadata'])
            self.assertIn('metadata', evidence['notRenderedAttributeIds'])

    def test_verified_pin_alias_matches_upstream_canonical_measurement(self):
        with tempfile.TemporaryDirectory() as directory:
            c,p,b,s,l,g,svg,m=self.setup_source(Path(directory))
            p['components'][0]['pins'][0]['name']='VDD'
            s['result']['components'][1]['pins'][0]['pinName']='VDD_1'
            source=build_layout_input(c,p,b,s,l,g,svg,text_measurements=m)
            self.assertEqual(source['connectivity']['components'][0]['pins'][0]['name'],'VDD_1')
            self.assertEqual(p['components'][0]['pins'][0]['name'],'VDD')

    def test_fixed_native_sheet_print_has_owner_without_attribute_id(self):
        with tempfile.TemporaryDirectory() as directory:
            c,p,b,s,l,g,svg,m=self.setup_source(Path(directory))
            svg.write_text(svg.read_text(encoding='utf-8').replace('</svg>', '<g c_partid="sheet"><text id="fixed">Schematic</text></g></svg>'), encoding='utf-8')
            m['svgSha256']=g['result']['sourceSha256']=file_digest(svg)
            m['texts'].append(dict(id='fixed',text='Schematic',bbox=[900,10,950,20]))
            source=build_layout_input(c,p,b,s,l,g,svg,text_measurements=m)
            from eso.native_text import validate
            self.assertEqual(validate(s,svg,m)[1]['fixedSheetTextIds'],['fixed'])

    def test_wrong_hash_or_coordinate_calibration_is_refused(self):
        for defect in ('hash', 'pin', 'transform', 'duplicate-id', 'unowned-text', 'missing-visible'):
            with tempfile.TemporaryDirectory() as directory:
                c,p,b,s,l,g,svg,m = self.setup_source(Path(directory))
                if defect == 'hash': m['svgSha256'] = 'bad'
                if defect == 'pin': s['result']['components'][1]['pins'][0]['x'] += 10
                if defect == 'transform': m['svgToEdaTransform'] = [1,0,0,1,0,0]
                if defect == 'duplicate-id': m['texts'].append(copy.deepcopy(m['texts'][0]))
                if defect == 'unowned-text': m['texts'].append(dict(id='unknown', text='EXTRA', bbox=[1,1,2,2]))
                if defect == 'missing-visible': m['texts'] = m['texts'][1:]
                with self.subTest(defect=defect), self.assertRaises(Refused):
                    build_layout_input(c,p,b,s,l,g,svg,text_measurements=m)

    def test_omitted_nullable_text_or_designator_cannot_pass(self):
        for designator in (False,True):
            with tempfile.TemporaryDirectory() as directory:
                c,p,b,s,l,g,svg,m=self.setup_source(Path(directory))
                if designator:
                    s['result']['pagePrimitives']['attributes'][0]['ValueVisible']=None
                    m['texts']=[]
                else: m['texts']=m['texts'][:1]
                with self.subTest(designator=designator), self.assertRaises(Refused):
                    build_layout_input(c,p,b,s,l,g,svg,text_measurements=m)

    def test_collect_automatically_measures_nullable_attributes(self):
        from unittest.mock import patch
        from eso.bridge import DesignBridge
        from eso.core import confirm, digest, load
        from test_bridge import FakeBridgeAdapter
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            c,p,b,s,l,g,svg,m=self.setup_source(root)
            class Adapter(FakeBridgeAdapter):
                def snapshot(self,target):
                    raw=super().snapshot(target)
                    if not self.placed: raw['result']['pagePrimitives']['attributes']=[]
                    return raw
                def run(self,args,**kwargs):
                    if args[:2] == ['sch','export-image']:
                        Path(args[args.index('--out')+1]).write_bytes(svg.read_bytes())
                        return {'ok':True,'context':s['context'],'result':{}}
                    return super().run(args,**kwargs)
            a=Adapter(c,p,b,s,l,g); bridge=DesignBridge(a,root/'stage')
            bridge.stage(c,confirm(c,digest(c),'SYNTHETIC HUMAN'),p,b)
            with patch('eso.native_text.render', return_value=m):
                self.assertEqual(bridge.collect()['state'],'layout-compiled')
            manifest=load(bridge.root/'measurement-evidence.json')['files']
            self.assertIn('native-text-measurements.json',manifest)
            self.assertEqual(load(bridge.root/'measured-snapshot.json'),s)
            from eso.workflow import Workflow
            bundle=Workflow(a,root/'run').prepare(c,confirm(c,digest(c),'SYNTHETIC HUMAN'),p,
                bridge.root/'composition.json',bridge.root/'measured-snapshot.json',bridge.root)
            self.assertEqual(bundle['state'],'prepared')


if __name__ == '__main__':
    unittest.main()
