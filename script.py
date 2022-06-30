import nmap
import os
import sys
i = 0
array_of_valid_ip = []
firmware = str(sys.argv[1])
print(firmware)


nm = nmap.PortScanner()




nm.scan(hosts='192.168.1.0/24', arguments='-n -sP')
hosts_list = [(x, nm[x]['status']['state']) for x in nm.all_hosts()]
print("ID DRONE ONLINE")

#catch ips
for host, status in hosts_list:
    arr = host.split(".")
    
    if(int(arr[3])>=11):
        print("ID DRONE : " + arr[3] + " is online !")
        array_of_valid_ip.append(host) #check last_byte of the ip_address , range is 11-255


#print(host)
print("OTA UPLOADING")
size = len(array_of_valid_ip)
print(" ")
print(" ")
#create string with ips

str_p= ""

for i in range(size):
    str_p +=(" -p " + array_of_valid_ip[i])

# send command to cmd
string = "poetry run ap-uploader " + (str_p) + " " + (firmware)
os.system('cmd /k '+string)


