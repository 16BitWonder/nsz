from nut import Print, SectionFs, aes128
import os
import json
import Fs
import Fs.Pfs0
import Fs.Type
import Fs.Nca
import Fs.Type
import subprocess
from contextlib import closing
import zstandard
from time import sleep
from tqdm import tqdm
from binascii import hexlify as hx, unhexlify as uhx
import hashlib


def solidCompress(filePath, compressionLevel = 17, outputDir = None, threads = -1):
	
	ncaHeaderSize = 0x4000
	
	filePath = os.path.abspath(filePath)
	container = Fs.factory(filePath)
	container.open(filePath, 'rb')
	
	CHUNK_SZ = 0x1000000
	
	if outputDir is None:
		nszPath = filePath[0:-1] + 'z'
	else:
		nszPath = os.path.join(outputDir, os.path.basename(filePath[0:-1] + 'z'))
		
	nszPath = os.path.abspath(nszPath)
	
	Print.info('compressing (level %d) %s -> %s' % (compressionLevel, filePath, nszPath))
	
	newNsp = Fs.Pfs0.Pfs0Stream(nszPath)
	
	for nspf in container:
		if isinstance(nspf, Fs.Nca.Nca) and nspf.header.contentType == Fs.Type.Content.DATA:
			Print.info('skipping delta fragment')
			continue
			
		if isinstance(nspf, Fs.Nca.Nca) and (nspf.header.contentType == Fs.Type.Content.PROGRAM or nspf.header.contentType == Fs.Type.Content.PUBLICDATA):
			if SectionFs.isNcaPacked(nspf, ncaHeaderSize):
				
				newFileName = nspf._path[0:-1] + 'z'
				
				f = newNsp.add(newFileName, nspf.size)
				
				start = f.tell()
				
				nspf.seek(0)
				f.write(nspf.read(ncaHeaderSize))
				
				sections = []
				for fs in SectionFs.sortedFs(nspf):
					sections += fs.getEncryptionSections()
				
				header = b'NCZSECTN'
				header += len(sections).to_bytes(8, 'little')
				
				i = 0
				for fs in sections:
					i += 1
					header += fs.offset.to_bytes(8, 'little')
					header += fs.size.to_bytes(8, 'little')
					header += fs.cryptoType.to_bytes(8, 'little')
					header += b'\x00' * 8
					header += fs.cryptoKey
					header += fs.cryptoCounter
					
				f.write(header)
				
				blockID = 0
				chunkRelativeBlockID = 0
				startChunkBlockID = 0
				blocksHeaderFilePos = f.tell()
				compressedblockSizeList = []
				
				decompressedBytes = ncaHeaderSize
				
				with tqdm(total=nspf.size, unit_scale=True, unit="B/s") as bar:
					
					partitions = []
					for section in sections:
						#print('offset: %x\t\tsize: %x\t\ttype: %d\t\tiv%s' % (section.offset, section.size, section.cryptoType, str(hx(section.cryptoCounter))))
						partitions.append(nspf.partition(offset = section.offset, size = section.size, n = None, cryptoType = section.cryptoType, cryptoKey = section.cryptoKey, cryptoCounter = bytearray(section.cryptoCounter), autoOpen = True))
						
					
					partNr = 0
					bar.update(f.tell())
					cctx = zstandard.ZstdCompressor(level=compressionLevel)
					compressor = cctx.stream_writer(f)
					while True:
					
						buffer = partitions[partNr].read(CHUNK_SZ)
						while (len(buffer) < CHUNK_SZ and partNr < len(partitions)-1):
							partNr += 1
							buffer += partitions[partNr].read(CHUNK_SZ - len(buffer))
						if len(buffer) == 0:
							break
						compressor.write(buffer)
						
						decompressedBytes += len(buffer)
						bar.update(len(buffer))
				
				compressor.flush(zstandard.FLUSH_FRAME)
				compressor.flush(zstandard.COMPRESSOBJ_FLUSH_FINISH)
				
				written = f.tell() - start
				print('compressed %d%% %d -> %d  - %s' % (int(written * 100 / nspf.size), decompressedBytes, written, nspf._path))
				newNsp.resize(newFileName, written)
				continue
			else:
				print('not packed!')

		f = newNsp.add(nspf._path, nspf.size)
		nspf.seek(0)
		while not nspf.eof():
			buffer = nspf.read(CHUNK_SZ)
			f.write(buffer)
	
	newNsp.close()