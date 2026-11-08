#include <stdio.h>
#include "checkin_button.h"
static int fails = 0;
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); ++fails; } } while (0)
bool edge(rescue::CheckinButton& b, bool down, uint32_t at, bool enabled=true) {
 b.update(down,at,enabled); return b.update(down,at+40,enabled);
}
bool sequence(rescue::CheckinButton& b,uint32_t t,uint32_t hold) {
 CHECK(!edge(b,true,t+100)); CHECK(!edge(b,false,t+200));
 CHECK(!edge(b,true,t+400)); CHECK(!edge(b,false,t+500));
 CHECK(!edge(b,true,t+700)); CHECK(!b.update(true,t+700+hold,true));
 return edge(b,false,t+700+hold);
}
int main() {
 rescue::CheckinButton b; b.begin(false,0);
 CHECK(sequence(b,0,2000)); CHECK(!b.update(false,5000,true));
 b.arm(false); CHECK(!sequence(b,6000,1999));
 b.arm(false); CHECK(!edge(b,true,10000)); CHECK(!edge(b,false,10100));
 CHECK(!edge(b,true,10400)); CHECK(!edge(b,false,12400)); // one click then hold is insufficient
 b.arm(false); CHECK(!edge(b,true,14000)); CHECK(!edge(b,false,14600)); // long first click
 CHECK(!edge(b,true,14900)); CHECK(!edge(b,false,15000));
 CHECK(!edge(b,true,15300)); CHECK(!edge(b,false,17300));
 b.arm(false); CHECK(!edge(b,true,18000)); CHECK(!edge(b,false,18100));
 CHECK(!edge(b,true,20200)); CHECK(!edge(b,false,20300)); // excessive gap
 CHECK(!edge(b,true,20600)); CHECK(!edge(b,false,22600));
 b.begin(false,24000);
 CHECK(!b.update(true,24010,true)); CHECK(!b.update(false,24020,true));
 CHECK(!b.update(true,24030,true)); CHECK(!b.update(false,24040,true));
 CHECK(!b.update(false,24100,true)); CHECK(!b.pressed());
 CHECK(sequence(b,25000,2100));
 b.begin(true,30000); b.arm(true); CHECK(!edge(b,false,32000));
 CHECK(sequence(b,33000,2000)); // held before prompt must be released first
 b.begin(false,38000);
 CHECK(!edge(b,true,38100,false)); CHECK(!edge(b,false,38200,false));
 CHECK(!edge(b,true,38400,false)); CHECK(!edge(b,false,38500,false));
 CHECK(!edge(b,true,38700,false)); CHECK(!edge(b,false,41000,false));
 b.arm(false); CHECK(sequence(b,0xFFFFFF00UL,2000));
 puts(fails ? "checkin button: FAILED" : "checkin button: ok"); return fails?1:0;
}
