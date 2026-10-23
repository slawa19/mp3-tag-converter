import tempfile
import unittest
from pathlib import Path
from mutagen.id3 import ID3,TIT2,TPE1,TXXX,COMM,APIC
import repair_library as r

class Tests(unittest.TestCase):
 def test_encoding(self):
  pairs=[('Квартет Гетьман','УКРАЇНА/Квартет Гетьман - 2000'),('Где ждёт любовь (1975)','131. Ариэль - Где ждёт любовь (1975).mp3')]
  for text,context in pairs:
   bad=text.encode('cp1251').decode('latin1')
   self.assertEqual(r.repair_text(bad,context)[0],text)
  for good in ['Ætli Það Sé Óhollt Að Láta Sig Dreyma','Beyoncé','Sigur Rós','Привіт світе','One For The Road']:
   self.assertEqual(r.repair_text(good,'music/'+good)[0],good)
  self.assertEqual(r.repair_text('FranÃ§ais','music')[0],'Français')
 def test_all_text_fields(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp); p=root/'Квартет Гетьман.mp3'; tags=ID3()
   import subprocess
   subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','anullsrc=r=44100:cl=stereo','-t','0.2',str(p)],check=True)
   bad='Квартет Гетьман'.encode('cp1251').decode('latin1')
   tags.add(TIT2(encoding=0,text=[bad])); tags.add(TPE1(encoding=0,text=[bad]))
   tags.add(TXXX(encoding=0,desc=bad,text=[bad])); tags.add(COMM(encoding=0,lang='eng',desc=bad,text=[bad]))
   tags.add(APIC(encoding=3,mime='image/jpeg',type=3,desc='cover',data=b'raw-picture'))
   tags.save(p)
   original=p.read_bytes()
   result=r.process(p,root,None,False,{})
   self.assertNotIn('error',result)
   self.assertEqual(len(result['changes']),6)
   self.assertEqual(p.read_bytes(),original)
   backup=root.parent/(root.name+'-backup')
   applied=r.process(p,root,backup,True,{})
   self.assertNotIn('error',applied)
   self.assertTrue(applied['audio_verified'])
   self.assertEqual((backup/p.name).read_bytes(),original)
   self.assertEqual(str(ID3(p)['TIT2']),'Квартет Гетьман')
   self.assertEqual(ID3(p).getall('APIC')[0].data,b'raw-picture')
   self.assertFalse(r.process(p,root,None,False,{})['changes'])
   import shutil
   shutil.rmtree(backup)
if __name__=='__main__':unittest.main()
