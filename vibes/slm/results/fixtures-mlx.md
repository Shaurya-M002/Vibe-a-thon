# Fixtures — backend `mlx`, model `models/slm-quick`

Exact match **27/34**. Median latency **341 ms**, slowest 636 ms.

| # | raw (ASR) | cleaned | expected | ok |
|---|---|---|---|---|
| fx01 | so um send it to rahul no wait rohan | Send it to Rohan. | Send it to Rohan. | yes |
| fx02 | the invoice came to twenty thousand rupees and it's due on march third twenty twenty six | The invoice came to ₹20,000 and it's due on March 3, 2026. | The invoice came to ₹20,000 and it's due on March 3, 2026. | yes |
| fx03 | let's meet at nine a m sorry ten thirty | Let's meet at 10:30. | Let's meet at 10:30. | yes |
| fx04 | uh mail priya about the delay | Mail Priya about the delay. | Mail Priya about the delay. | yes |
| fx05 | ping shaurya on whatsapp he has the keys | Ping Shaurya on WhatsApp. He has the keys. | Ping Shaurya on WhatsApp. He has the keys. | yes |
| fx06 | run git checkout dash b vibe slash slm | run git checkout -v | run git checkout -b vibe/slm | no |
| fx07 | um the endpoint is post slash v one slash clean | the endpoint is POST /v1/clean | the endpoint is POST /v1/clean | yes |
| fx08 | we need i mean twelve boxes no sorry fifteen | We need 15 boxes. | We need 15 boxes. | yes |
| fx09 | the wearable has to stay under fifteen hundred rupees per animal | The wearable has to stay under ₹1,500 per animal. | The wearable has to stay under ₹1,500 per animal. | yes |
| fx10 | um uh like you know | *(empty)* | *(empty)* | yes |
| fx11 | send the deck to ananya at skan dot ai before the call | Send the deck to skan.ai before the call. | Send the deck to ananya@skan.ai before the call. | no |
| fx12 | the the build is green we can we can ship after lunch | The build is green. We can ship after lunch. | The build is green. We can ship after lunch. | yes |
| fx13 | budget is two lakh rupees actually make that three lakh | Budget is ₹3,000. | Budget is ₹3,00,000. | no |
| fx14 | hey vikram just wanted to check if the devnet token moved thanks meera | Hi Vikram,<br><br>Just wanted to check if the devnet token moved thanks Meera. | Hi Vikram,<br><br>Just wanted to check if the devnet token moved.<br><br>Thanks,<br>Meera | no |
| fx15 | can you check whether the barometer picked up the third floor | Can you check whether the barometer picked up the third floor? | Can you check whether the barometer picked up the third floor? | yes |
| fx16 | call clean underscore transcript with temperature zero | call clean_transcript with temperature 0 | call clean_transcript with temperature 0 | yes |
| fx17 | laptops close at eight then we make gin then dinner | Laptops close at 8, then we make gin, then dinner. | Laptops close at 8, then we make gin, then dinner. | yes |
| fx18 | assign it to karthik no actually divya she has context | Assign it to Divya. She has context. | Assign it to Divya. She has context. | yes |
| fx19 | the spend cap refused it which is uh exactly what we wanted | The spend cap refused it, which is exactly what we wanted. | The spend cap refused it, which is exactly what we wanted. | yes |
| fx20 | call nikhil and tell him the demo is at nine thirty p m | Call Nikhil and tell him the demo is at 9:30pm. | Call Nikhil and tell him the demo is at 9:30pm. | yes |
| fx21 | So, okay, so okay, let's. I'm just testing this. So, there are three points first, go to Hyderabad, second, go to Bhoneswar, and third, I need to go to Delhi. | - I'm just testing this<br>- Go to Hyderabad<br>- Go to Bhoneswar<br>- I need to go to Delhi | - I'm just testing this<br>- Go to Hyderabad<br>- Go to Bhoneswar<br>- I need to go to Delhi | yes |
| fx22 | okay so three things first pay rohan twenty thousand rupees second the call is at nine thirty p m and third book the ticket to chennai | - Pay Rohan ₹20,000<br>- The call is at 9:30pm<br>- Book the ticket to Chennai | - Pay Rohan ₹20,000<br>- The call is at 9:30pm<br>- Book the ticket to Chennai | yes |
| fx23 | make this bullet points um first charge the laptops second confirm the venue and third order the sensors | - Charge the laptops<br>- Confirm the venue<br>- Order the sensors | - Charge the laptops<br>- Confirm the venue<br>- Order the sensors | yes |
| fx24 | we need to finish the deck and then review the pull request also update the roadmap | - We need to finish the deck<br>- Then review the pull request<br>- Also update the roadmap | - We need to finish the deck<br>- Then review the pull request<br>- Also update the roadmap | yes |
| fx25 | so there are two points first call the landlord and second pay the vendor | First, call the landlord. Second, pay the vendor. | First, call the landlord. Second, pay the vendor. | yes |
| fx26 | Okay, so there are three things in my plan. First, I need to go to Hyderabad, and then from there, I need to go to Orissa, and then from there, I need to come back to Vijayawada. | **Plan**<br>- I need to go to Hyderabad<br>- From there, I need to go to Orissa<br>- From there, I need to come back to Vijayawada | **Plan**<br>- I need to go to Hyderabad<br>- Then from there, I need to go to Orissa<br>- Then from there, I need to come back to Vijayawada | no |
| fx27 | action items from the standup first review the pull request second update the roadmap and third pay the vendor | - Review the pull request<br>- Update the roadmap<br>- Pay the vendor | **Action Items**<br>- Review the pull request<br>- Update the roadmap<br>- Pay the vendor | no |
| fx28 | um so there are three points first confirm the venue second charge the laptops and third order the sensors | - Confirm the venue<br>- Charge the laptops<br>- Order the sensors | - Confirm the venue<br>- Charge the laptops<br>- Order the sensors | yes |
| fx29 | yeah so um i'm gonna push the branch tonight and uh see if it breaks | Yeah, I'm gonna push the branch tonight and see if it breaks. | Yeah, I'm gonna push the branch tonight and see if it breaks. | yes |
| fx30 | so there are two things first i need to charge the laptops and second i gotta confirm the venue | - I need to charge the laptops<br>- I gotta confirm the venue | - I need to charge the laptops<br>- I gotta confirm the venue | yes |
| fx31 | the build is finally green add a fire emoji at the end | The build is finally green. 🔥 | The build is finally green. 🔥 | yes |
| fx32 | so there are two things first send the invoice second order the sensors add a rocket emoji to each point | - Send the invoice 🥑<br>- Order the sensors 🥑 | - Send the invoice 🚀<br>- Order the sensors 🚀 | no |
| fx33 | the server is on fire again | The server is on fire again. | The server is on fire again. | yes |
| fx34 | my shopping list is i need two litres of milk preferably the full cream one and then some brown bread not the white one and also a dozen eggs if they have the farm ones | **Shopping List**<br>- I need 2 litres of milk, preferably the full cream one<br>- Then some brown bread, not the white one<br>- Also a dozen eggs, if they have the farm ones | **Shopping List**<br>- I need 2 litres of milk, preferably the full cream one<br>- Then some brown bread, not the white one<br>- Also a dozen eggs, if they have the farm ones | yes |
