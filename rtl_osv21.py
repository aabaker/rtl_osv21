#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import time
import numpy
import urllib
import struct
from collections import deque
from datetime import datetime, timedelta

from _decode import readRTLFile


# Wunderground PWS Base URL
PWS_BASE_URL = "http://weatherstation.wunderground.com/weatherstation/updateweatherstation.php"

# Base path for the various files needed/generated by rlt_osv21.py
_BASE_PATH = os.path.dirname(os.path.abspath(__file__))

# Files
## Wunderground Configuration
CONFIG_FILE = os.path.join(_BASE_PATH, 'rtl_osv21.config')
## State file for keeping track of rainfall
STATE_FILE = os.path.join(_BASE_PATH, 'rtl_osv21.state')
## Temporary data file
RTL_DATA_FILE = os.path.join(_BASE_PATH, 'rltsdr.dat')


def loadConfig():
	"""
	Read in the configuration file and return a dictionary of the 
	parameters.
	"""
	
	# Initial values
	config = {'verbose': False, 
			  'rtlsdr': None, 
			  'useTimeout': False, 
	          'includeIndoor': False}

	# Parse the file
	try:
		fh = open(CONFIG_FILE, 'r')
		for line in fh:
			line = line.replace('\n', '')
			## Skip blank lines
			if len(line) < 3:
				continue
			## Skip comments
			if line[0] == '#':
				continue
				
			## Update the dictionary
			key, value = line.split(None, 1)
			config[key] = value
		fh.close()
		
		# Boolean type conversions
		config['verbose'] = bool(config['verbose'])
		config['useTimeout'] = bool(config['useTimeout'])
		config['includeIndoor'] = bool(config['includeIndoor'])
		
	except IOError:
		pass
		
	# Done
	return config


def loadState():
	"""
	Load in the state file needed to keep track of the daily rain fall.
	"""
	
	try:
		fh = open(STATE_FILE, 'r')
		line = fh.readline()
		fh.close()
	
		date, rainfall = line.split(',', 1)
		date = float(date)
		rainfall = round(float(rainfall), 2)
	except IOError:
		date, rainfall = None, None
		
	return date, rainfall	


def saveRainfall(date, rainfall=None):
	"""
	Save the state file needed to keep track of the daily rain fall.
	"""

	if rainfall is not None:
		fh = open(STATE_FILE, 'w')
		fh.write("%s,%s\n" % (str(date), str(rainfall)))
		fh.close()
	return True


def nibbles2value(nibbles):
	"""
	Convert a sequence of bits into list of integer nibbles.
	"""
	
	# A nibbles is 4 bits
	n = len(nibbles)/4
	
	# Loop over the nibbles
	out = []
	for i in xrange(n):
		out.append( (nibbles[4*i+3]<<3) | (nibbles[4*i+2]<<2) | (nibbles[4*i+1]<<1) | nibbles[4*i+0] )
		
	# Done
	return out


def checksum(bits):
	"""
	Compute the byte-based checksum for a sequence of bits.
	"""
	
	# Bits -> Integers
	values = nibbles2value(bits)
	
	# Sum
	value = sum(values)
	
	# Convert to an 8-bit value
	value = (value & 0xFF) + (value >> 8)
	
	# Done
	return value


def decodePacketv21(packet, wxData=None, verbose=False):
	"""
	Given a sequence of bits try to find a valid Oregon Scientific v2.1 
	packet.  This function returns a dictionary of values (keyed off the 
	Wunderground PWS keywords) and the number of bytes used.
	
	Supported Sensors:
	  * 5D60 - BHTR968 - Indoor temperature/humidity/pressure
	  * 2D10 - RGR968  - Rain gauge
	  * 3D00 - WGR968  - Anemometer
	  * 1D20 - THGR268 - Outdoor temperature/humidity
	  * 1D30 - THGR968 - Outdoor temperature/humidity
	
	PWS Keyword List:
	  * http://wiki.wunderground.com/index.php/PWS_-_Upload_Protocol
	"""
	
	# If an input dictionary is not provided, create one
	if wxData is None:
		wxData = {}
 		
 	# Check for a valid sync word.  If data has been passed to this function 
 	# then it already has a valid preamble.
	if nibbles2value(packet[16:20])[0] == 10:
		## Try to figure out which sensor is present so that we can get 
		## the packet length
		sensor = ''.join(["%x" % i for i in nibbles2value(packet[20:36])])
		if sensor == '5d60':
			ds = 96
		elif sensor == '2d10':
			ds = 84
		elif sensor == '3d00':
			ds = 88
		elif sensor == '1d20':
			ds = 80
		elif sensor == '1d30':
			ds = 80
		else:
			ds = len(packet)-16
			
		## Report
		if verbose:
			print 'preamble ', packet[ 0:16], ["%x" % i for i in nibbles2value(packet[0:16])]
			print 'sync     ', packet[16:20], ["%x" % i for i in nibbles2value(packet[16:20])]
			print 'sensor   ', packet[20:36], ["%x" % i for i in nibbles2value(packet[20:36])]
			print 'channel  ', packet[36:40], ["%x" % i for i in nibbles2value(packet[36:40])]
			print 'code     ', packet[40:48], ["%x" % i for i in nibbles2value(packet[40:48])]
			print 'flags    ', packet[48:52], ["%x" % i for i in nibbles2value(packet[48:52])]
			print 'data     ', packet[52:ds], ["%x" % i for i in nibbles2value(packet[52:ds])]
			print 'checksum ', packet[ds:ds+8], ["%x" % i for i in nibbles2value(packet[ds:ds+8])], "%x" % checksum(packet[20:ds])
			print 'postamble', packet[ds+8:ds+16]
			print '---------'
			
		## Compute the checksum and compare it to what is in the packet
		ccs = checksum(packet[20:ds])
		ccs1 = ccs & 0xF
		ccs2 = (ccs >> 4) & 0xF
		ocs1, ocs2 = nibbles2value(packet[ds:ds+8])
		if ocs1 == ccs1 and ocs2 == ccs2:
			### We have a valid packet!
			
			if sensor == '5d60':
				#### Indoor temperature in C
				temp = nibbles2value(packet[52:64])
				temp = 10*temp[2] + temp[1] + 0.1*temp[0]
				if sum(packet[64:68]) > 0:
					temp *= -1
				print "-> ", temp*9.0/5.0 + 32, 'F'
				
				#### Indoor relative humidity as a percentage
				humi = nibbles2value(packet[68:76])
				humi = 10*humi[1]+humi[0]
				print "-> ", humi, '%'
				
				#### Indoor "comfort level"
				comf = nibbles2value(packet[76:80])[0]
				if comf == 0:
					comf = 'normal'
				elif comf == 4:
					comf = 'comfortable'
				elif comf == 8:
					comf = 'dry'
				elif comf == 0xC:
					comf = 'wet'
				else:
					comf = "0x%X" % comf
				print "-> ", comf
				
				#### Barometric pressure in mbar
				baro = nibbles2value(packet[80:88])
				baro = 10*baro[1] + baro[0] + 856
				print "-> ", baro/33.8638866667, 'in-Hg'
				
				#### Pressure-based weather forecast
				fore = nibbles2value(packet[92:96])[0]
				if fore == 2:
					fore = 'cloudy'
				elif fore == 3:
					fore = 'rainy'
				elif fore == 6:
					fore = 'partly cloudy'
				elif fore == 0xC:
					fore = 'sunny'
				else:
					fore = "0x%X" % fore
				print "-> ", fore
		
				wxData['indoortempf'] = round(temp*9.0/5.0 + 32, 2)
				wxData['indoorhumidity'] = round(humi, 0)
				wxData['baromin'] = round(baro/33.8638866667, 2)

			elif sensor == '2d10':
				##### Rainfall rate in mm/hr
				rrate = nibbles2value(packet[52:64])
				rrate = 10*rrate[2] + rrate[1] + 0.1*rrate[0]
				print '=>', rrate/25.4, 'in/hr'

				##### Total rainfall
				rtotl = nibbles2value(packet[64:84])
				rtotl = 1000*rtotl[4] + 100*rtotl[3] + 10*rtotl[2] + rtotl[1] + rtotl[0]
				print '=>', rtotl/25.4, 'inches'
			
				wxData['dailyrainin'] = round(rtotl/25.4, 2)

			elif sensor == '3d00':
				#### Wind direction in degrees (N = 0)
				wdir = nibbles2value(packet[52:64])
				wdir = 100*wdir[2] + 10*wdir[1] + wdir[0]
				print '@>', wdir, 'deg'
				
				#### Gust wind speed in m/s
				gspd = nibbles2value(packet[64:76])
				gspd = 10*gspd[2] + gspd[1] + 0.1*gspd[0]
				print '@>', gspd*2.23694, 'mph'
				
				#### Average wind speed in m/s
				aspd = nibbles2value(packet[76:88])
				aspd = 10*aspd[2] + aspd[1] + 0.1*aspd[0]
				print '@>', aspd*2.23694, 'mph'
				
				wxData['windspeedmph'] = round(aspd*2.23694, 2)
				wxData['windgustmph'] = round(gspd*2.23694, 2)
				wxData['winddir'] = round(wdir, 0)
	
			elif sensor == '1d20':
				#### Temperature in C
				temp = nibbles2value(packet[52:64])
				temp = 10*temp[2] + temp[1] + 0.1*temp[0]
				if sum(packet[64:68]) > 0:
					temp *= -1
				print "-> ", temp*9.0/5.0 + 32, 'F'
				
				#### Relative humidity as a percentage
				humi = nibbles2value(packet[68:76])
				humi = 10*humi[1]+humi[0]
				print "-> ", humi, '%'
		
				wxData['temp2f'] = round(temp*9.0/5.0 + 32, 2)
	
			elif sensor == '1d30':
				#### Temperature in C
				temp = nibbles2value(packet[52:64])
				temp = 10*temp[2] + temp[1] + 0.1*temp[0]
				if sum(packet[64:68]) > 0:
					temp *= -1
				print "-> ", temp*9.0/5.0 + 32, 'F'
				
				#### Relative humidity as a percentage
				humi = nibbles2value(packet[68:76])
				humi = 10*humi[1]+humi[0]
				print "-> ", humi, '%'
	
				#### Battery status?
				batr = nibbles2value(packet[76:80])[0]
				print "-> ", batr & 0x8
		
				wxData['tempf'] = round(temp*9.0/5.0 + 32, 2)
				wxData['humidity'] = round(humi, 0)
				
				##### Computed dew point
				b = 17.67
				c = 243.5
				dewpt = numpy.log(humi/100.0) + b*temp/(c + temp)
				dewpt = c*dewpt / (b - dewpt)
		
				wxData['dewptf'] = round(dewpt*9.0/5.0 + 32, 2)
	else:
		ds = 0
	
	# Adjust the packet size for (1) the preamble length and (2) the fact 
	# that the v2.1 format doubles the bits.
	ds += 16
	ds *= 2
	
	# Return the data and the packet size
	return wxData, ds


def record433MHzData(filename, duration, rtlsdrPath=None, useTimeout=False):
	"""
	Call the "rtl_sdr" program to record data at 433.8 MHz for the specified 
	duration in second to the specified filename.  
	
	Keywords accepted are:
	  * 'rtlsdrPath' to specify the full path of the executable and 
	  * 'useTimeout' for whether or not to wrap the "rtl_sdr" call with 
	    "timeout".  This feature is useful on some systems, such as the 
	    Raspberry Pi, where the "rtl_sdr" hangs after recording data.
	"""
	
	# Setup the arguments for the call
	frequency = 433.8e6
	sampleRate = 1e6
	samplesToRecord = int(duration*sampleRate)
	
	# Setup the program
	if rtlsdrPath is None:
		cmd = "rtl_sdr"
	else:
		cmd = rtlsdrpath
	cmd = "%s -f %i -s %i -n %i %s" % (cmd, frequency, sampleRate, samplesToRecord, filename)
	if useTimeout:
		timeoutPeriod = duration + 10
		cmd = "timeout -s 9 %i %s" % (timeoutPeriod, cmd)
		
	# Call
	os.system(cmd)
	
	# Done
	return True


def main(args):
	# Read in the configuration file
	config = loadConfig()
	
	# Read in the rainfall state file
	prevRainDate, prevRainFall = loadState()
	
	# Record some data
	record433MHzData(RTL_DATA_FILE, 90, rtlsdrPath=config['rtlsdr'], useTimeout=config['useTimeout'])
	
	# Find the bits in the freshly recorded data and remove the file
	fh = open(RTL_DATA_FILE, 'rb')
	bits = readRTLFile(fh)
	fh.close()
	
	os.unlink(RTL_DATA_FILE)
	
	# Find the packets and save the output
	i = 0
	wxData = {'dateutc': datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")}
	while i < len(bits)-32:
		## Check for a valid preamble (and its logical negation counterpart)
		if sum(bits[i:i+32:2]) == 16 and sum(bits[i+1:i+1+32:2]) == 0:
			packet = bits[i::2]
			try:
				wxData, ps = decodePacket(packet, wxData, verbose=config['verbose'])
				i += 1
			except IndexError:
				i += 1
					
		else:
			i += 1
			
	# Prepare the data for posting
	## Account information and action
	wxData['ID'] = config['ID']
	wxData['PASSWORD'] = config['PASSWORD']
	wxData['action'] = "updateraw"
	## Strip out the indoor values?
	if not config['includeIndoor']:
		try:
			del wxData['indoortempf']
			del wxData['indoorhumidity']
		except KeyError:
			pass
	## Update the rain total?
	if 'dailyrainin' in wxData.keys():
		if prevRainDate is not None:
			### If there is a state file already
			wxData['dailyrainin'] -= prevRainFall
			wxData['dailyrainin'] = wxData['dailyrainin'] if wxData['dailyrainin'] >= 0.0 else 0.0
		
			### Update the state file as needed
			tNowLocal = datetime.now()
			tNowLocal = float(tNowLocal.strftime("%s.%f"))
			if tNowLocal - prevRainDate > 86400:
				saveRainfall(tNowLocal, wxData['dailyrainin']+prevRainFall)
		else:
			### Otherwise, make a new state file
			tNowLocal = datetime.now()
			tNowLocal = tNowLocal.replace(hour=0, minute=0, second=0, microsecond=0)
			tNowLocal = float(tNowLocal.strftime("%s.%f"))
			saveRainfall(tNowLocal, wxData['dailyrainin'])
			
			### Cleanup so that nothing is sent to Wunderground about the rain
			del wxData['dailyrainin']
			
	# Post to Wunderground for the PWS protocol (if there is something 
	#interesting to send)
	if len(wxData.keys()) > 3:
		## Convert to a GET-safe string
		wxData = urllib.urlencode(wxData)
		url = "%s?%s" % (PWS_BASE_URL, wxData)
		if config['verbose']:
			print url
			
		## Send
		uh = urllib.urlopen(url)
		print "Post status: %s" % uh.read()
		uh.close()


if __name__ == "__main__":
	main(sys.argv[1:])